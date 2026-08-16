"""
Synthetic data generation with independently controllable skew, functional
dependency, and physical-order correlation.

The point of generating rather than using a fixed benchmark is that each factor
we want to attribute an effect to has to be varied in isolation, and real
datasets do not let you do that. Every generated table is written to CSV and
bulk loaded with COPY.

All randomness is seeded, so the whole corpus is byte-for-byte reproducible.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from config import DatasetSpec

# Deterministic map used to induce a functional dependency b = g(a).
# Chosen to be non-identity so that the dependency is not trivially visible
# in the data, while remaining exactly reproducible.
_DEP_MULT = 31
_DEP_ADD = 17

# Base epoch for the ts column, as a Unix timestamp (2020-01-01T00:00:00Z).
_TS_BASE = 1_577_836_800
_TS_SPAN = 5 * 365 * 24 * 3600  # five years of range


def _zipf_values(n: int, domain: int, s: float, rng: np.random.Generator) -> np.ndarray:
    """Draw n values from {1..domain} with p(i) proportional to 1/i**s.

    numpy's built-in zipf draws from an unbounded support, which would make the
    effective domain size depend on n. We build the truncated CDF explicitly so
    that the number of distinct values is fixed by `domain` and only the shape
    changes with s.
    """
    if s == 0.0:
        return rng.integers(1, domain + 1, size=n, dtype=np.int64)

    ranks = np.arange(1, domain + 1, dtype=np.float64)
    weights = 1.0 / np.power(ranks, s)
    cdf = np.cumsum(weights)
    cdf /= cdf[-1]

    u = rng.random(n)
    # searchsorted gives the rank index; +1 converts to a 1-based value.
    return (np.searchsorted(cdf, u, side="left") + 1).astype(np.int64)


def _dependent_pair(
    n: int, domain_a: int, domain_b: int, strength: float, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Generate (a, b) where b is a deterministic function of a with the given
    probability, and independent uniform otherwise.

    strength = 0.0 gives statistical independence.
    strength = 1.0 gives a hard functional dependency a -> b.

    This is precisely the structure PostgreSQL's extended statistics
    'dependencies' option is designed to capture, which makes it the right
    stimulus for testing whether that feature actually fixes plan selection.
    """
    a = rng.integers(0, domain_a, size=n, dtype=np.int64)
    b_dependent = (a * _DEP_MULT + _DEP_ADD) % domain_b
    b_independent = rng.integers(0, domain_b, size=n, dtype=np.int64)

    follows = rng.random(n) < strength
    b = np.where(follows, b_dependent, b_independent)
    return a, b


def _correlated_timestamps(
    n: int, corr: float, rng: np.random.Generator
) -> np.ndarray:
    """Timestamps whose value order correlates with physical row order.

    corr = 1.0 gives a strictly increasing column (BRIN's best case).
    corr = 0.0 gives random order (BRIN's worst case).

    Intermediate values are produced by shuffling a random subset of positions,
    which yields a monotonic relationship between `corr` and the realised rank
    correlation. We measure the realised value rather than assuming it.
    """
    sorted_ts = np.sort(rng.integers(_TS_BASE, _TS_BASE + _TS_SPAN, size=n, dtype=np.int64))

    if corr >= 1.0:
        return sorted_ts
    if corr <= 0.0:
        rng.shuffle(sorted_ts)
        return sorted_ts

    # Displace a (1 - corr) fraction of positions by permuting them together.
    n_shuffle = int(round((1.0 - corr) * n))
    if n_shuffle < 2:
        return sorted_ts
    idx = rng.choice(n, size=n_shuffle, replace=False)
    permuted = rng.permutation(idx)
    out = sorted_ts.copy()
    out[idx] = sorted_ts[permuted]
    return out


@dataclass
class GeneratedDataset:
    """Everything downstream code needs to know about one generated table."""

    spec: DatasetSpec
    csv_path: Path
    # Exact frequency tables, used to pick predicate constants that hit a
    # target selectivity precisely rather than approximately.
    skew_counts: np.ndarray  # index = value, entry = row count
    a_counts: np.ndarray
    ts_sorted: np.ndarray
    realised_ts_rank_corr: float
    n_rows: int
    # Retained so the query builder can compute the exact number of rows any
    # candidate predicate returns. Ground-truth cardinality is what the whole
    # study rests on, so we take it from the data rather than from the DBMS.
    k_skew: np.ndarray
    a: np.ndarray
    b: np.ndarray


def generate(spec: DatasetSpec, out_dir: Path) -> GeneratedDataset:
    """Materialise one dataset to CSV and return its metadata."""
    rng = np.random.default_rng(spec.seed)
    n = spec.n_rows

    k_uniform = rng.integers(1, spec.domain_skew + 1, size=n, dtype=np.int64)
    k_skew = _zipf_values(n, spec.domain_skew, spec.zipf_s, rng)
    a, b = _dependent_pair(n, spec.domain_a, spec.domain_b, spec.dep_strength, rng)
    ts = _correlated_timestamps(n, spec.physical_corr, rng)

    # Realised rank correlation between ts value and physical position. This is
    # the ground truth we later compare against PostgreSQL's own pg_stats
    # correlation estimate.
    positions = np.arange(n)
    realised_corr = float(
        np.corrcoef(positions, np.argsort(np.argsort(ts)))[0, 1]
    )

    # A payload column so rows have realistic width. Without it the table is so
    # narrow that a sequential scan is almost always cheapest and the
    # access-path decision becomes uninteresting.
    payload_pool = np.array(
        ["".join(rng.choice(list("abcdefghijklmnopqrstuvwxyz"), size=48)) for _ in range(256)]
    )
    payload_idx = rng.integers(0, len(payload_pool), size=n)

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{spec.table}.csv"

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        # Chunked write keeps peak memory bounded on the 10M-row configs.
        chunk = 100_000
        for start in range(0, n, chunk):
            end = min(start + chunk, n)
            rows = zip(
                range(start + 1, end + 1),
                k_uniform[start:end].tolist(),
                k_skew[start:end].tolist(),
                a[start:end].tolist(),
                b[start:end].tolist(),
                ts[start:end].tolist(),
                payload_pool[payload_idx[start:end]].tolist(),
            )
            writer.writerows(rows)

    skew_counts = np.bincount(k_skew, minlength=spec.domain_skew + 1)
    a_counts = np.bincount(a, minlength=spec.domain_a)

    return GeneratedDataset(
        spec=spec,
        csv_path=csv_path,
        skew_counts=skew_counts,
        a_counts=a_counts,
        ts_sorted=np.sort(ts),
        realised_ts_rank_corr=realised_corr,
        n_rows=n,
        k_skew=k_skew,
        a=a,
        b=b,
    )

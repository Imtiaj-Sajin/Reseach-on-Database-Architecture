"""
Query construction with exactly known ground-truth cardinality.

The central measurement of this study is the gap between what the planner
thinks a predicate returns and what it actually returns. That is only
meaningful if we know the true answer independently of the DBMS, so every
predicate constant here is chosen by counting the generated arrays directly.

Each query records both its *target* selectivity and its *achieved*
selectivity. They differ for equality predicates on a discrete column, because
a single value has whatever frequency it has. We report the achieved value
everywhere and never the target.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

import datagen
from datagen import GeneratedDataset


@dataclass(frozen=True)
class Query:
    qid: str
    family: str  # eq | range | conj | ts_range
    sql: str
    target_selectivity: float
    true_rows: int
    n_rows: int
    # Free-form record of the constants used, for the appendix.
    params: dict

    @property
    def achieved_selectivity(self) -> float:
        return self.true_rows / self.n_rows

    def to_dict(self) -> dict:
        d = asdict(self)
        d["achieved_selectivity"] = self.achieved_selectivity
        return d


def _closest_value_by_frequency(counts: np.ndarray, target_rows: int) -> tuple[int, int]:
    """Pick the value whose row count is closest to target_rows.

    Returns (value, actual_count). Values with zero occurrences are excluded,
    since a predicate matching nothing exercises a degenerate plan.
    """
    present = np.nonzero(counts)[0]
    if present.size == 0:
        raise ValueError("column has no values")
    diffs = np.abs(counts[present].astype(np.int64) - target_rows)
    best = present[int(np.argmin(diffs))]
    return int(best), int(counts[best])


def _range_bounds_for_target(
    sorted_values: np.ndarray, target_rows: int
) -> tuple[int, int, int]:
    """Pick [lo, hi] over a sorted array that contains about target_rows rows.

    We anchor the window at the median so that different selectivities are
    centred on the same part of the distribution and are therefore comparable.
    """
    n = sorted_values.size
    target_rows = max(1, min(target_rows, n))
    centre = n // 2
    half = target_rows // 2
    start = max(0, centre - half)
    end = min(n, start + target_rows)
    start = max(0, end - target_rows)

    lo = int(sorted_values[start])
    hi = int(sorted_values[end - 1])
    # Recount exactly, because ties at the boundaries mean the window width is
    # not necessarily the row count.
    actual = int(np.searchsorted(sorted_values, hi, side="right") - np.searchsorted(sorted_values, lo, side="left"))
    return lo, hi, actual


def build_queries(ds: GeneratedDataset, targets: tuple[float, ...]) -> list[Query]:
    """Build the full query set for one dataset."""
    t = ds.spec.table
    n = ds.n_rows
    out: list[Query] = []

    skew_sorted = np.sort(ds.k_skew)

    # ---- Family 1: equality on the skewed column ---------------------------
    # Hash, B-Tree, BRIN and a sequential scan are all candidates here.
    #
    # Selectivity is not a free parameter for an equality predicate on a
    # discrete column: a value returns whatever number of rows it happens to
    # have. Sweeping target selectivities would therefore keep resolving to the
    # same value on uniform data and produce duplicate queries. Instead we pick
    # values spread across the frequency distribution, which under a Zipfian
    # column spans several orders of magnitude of selectivity and under a
    # uniform column correctly collapses to a single narrow band.
    present = np.nonzero(ds.skew_counts)[0]
    if present.size:
        by_freq = present[np.argsort(ds.skew_counts[present])]
        picks = np.unique(
            by_freq[np.linspace(0, by_freq.size - 1, num=min(8, by_freq.size)).astype(int)]
        )
        for val in picks.tolist():
            out.append(
                Query(
                    qid=f"eq_v{val}",
                    family="eq",
                    sql=f"SELECT * FROM {t} WHERE k_skew = {val}",
                    target_selectivity=float("nan"),
                    true_rows=int(ds.skew_counts[val]),
                    n_rows=n,
                    params={"k_skew": int(val)},
                )
            )

    for target in targets:
        target_rows = max(1, int(round(target * n)))

        # ---- Family 2: range on the skewed column --------------------------
        # Hash cannot serve this, so the candidate set shrinks. This tests
        # whether the planner correctly falls back.
        lo, hi, actual = _range_bounds_for_target(skew_sorted, target_rows)
        out.append(
            Query(
                qid=f"range_{target}",
                family="range",
                sql=f"SELECT * FROM {t} WHERE k_skew BETWEEN {lo} AND {hi}",
                target_selectivity=target,
                true_rows=actual,
                n_rows=n,
                params={"lo": lo, "hi": hi},
            )
        )

        # ---- Family 4: range on the physically correlated column -----------
        # This is BRIN's home ground. At high physical correlation BRIN should
        # win decisively; at low correlation it should lose badly. Whether the
        # planner tracks that switch is the question.
        lo_ts, hi_ts, actual_ts = _range_bounds_for_target(ds.ts_sorted, target_rows)
        out.append(
            Query(
                qid=f"tsrange_{target}",
                family="ts_range",
                sql=f"SELECT * FROM {t} WHERE ts BETWEEN {lo_ts} AND {hi_ts}",
                target_selectivity=target,
                true_rows=actual_ts,
                n_rows=n,
                params={"lo": lo_ts, "hi": hi_ts},
            )
        )

    # ---- Family 3: conjunctive predicates on the correlated pair -----------
    # Selectivity is not a free parameter here, because the joint distribution
    # of (a, b) is fixed by the dataset. Instead we enumerate a fixed set of
    # `a` values spanning the frequency range, and for each we build both the
    # dependent and the independent partner predicate.
    #
    # The dependent variant is the interesting one: PostgreSQL multiplies the
    # two per-column selectivities under an independence assumption, so as the
    # functional dependency strengthens its estimate falls further below the
    # truth while the true count stays put.
    a_present = np.nonzero(ds.a_counts)[0]
    if a_present.size:
        # Sample a spread of `a` values by frequency rank.
        ranked = a_present[np.argsort(ds.a_counts[a_present])]
        picks = np.unique(
            ranked[np.linspace(0, ranked.size - 1, num=min(5, ranked.size)).astype(int)]
        )
        for va in picks.tolist():
            mask_a = ds.a == va

            # Dependent partner: the value b takes whenever the dependency
            # holds. Imported from datagen so the two definitions cannot drift.
            vb_dep = int((va * datagen._DEP_MULT + datagen._DEP_ADD) % ds.spec.domain_b)
            true_dep = int(np.count_nonzero(mask_a & (ds.b == vb_dep)))
            out.append(
                Query(
                    qid=f"conj_dep_a{va}",
                    family="conj",
                    sql=f"SELECT * FROM {t} WHERE a = {va} AND b = {vb_dep}",
                    target_selectivity=float("nan"),
                    true_rows=true_dep,
                    n_rows=n,
                    params={"a": int(va), "b": vb_dep, "variant": "dependent"},
                )
            )

            # Independent control: a b value that is not the dependent partner.
            vb_indep = int((vb_dep + ds.spec.domain_b // 2) % ds.spec.domain_b)
            true_indep = int(np.count_nonzero(mask_a & (ds.b == vb_indep)))
            out.append(
                Query(
                    qid=f"conj_indep_a{va}",
                    family="conj",
                    sql=f"SELECT * FROM {t} WHERE a = {va} AND b = {vb_indep}",
                    target_selectivity=float("nan"),
                    true_rows=true_indep,
                    n_rows=n,
                    params={"a": int(va), "b": vb_indep, "variant": "independent"},
                )
            )

    return out

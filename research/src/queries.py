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


def _range_bounds_for_target(
    sorted_values: np.ndarray, target_rows: int
) -> tuple[int, int, int]:
    """Pick [lo, hi] whose row count is as close as possible to target_rows.

    A predicate on a discrete column can only return whole values, so the
    search runs over the value domain rather than over row positions.

    An earlier version anchored a fixed-width window at the median row
    position. That silently failed on skewed columns: under a Zipfian
    distribution the median row sits inside a single very frequent value, so
    lo and hi collapsed onto that one value and the predicate returned its
    entire frequency, millions of rows, no matter what target was requested.
    Every target below that frequency produced an identical query.

    This version sweeps two pointers over the distinct values and their
    cumulative counts, choosing the contiguous value range whose total is
    nearest the target. On a uniform column it behaves like the old version;
    on a skewed one it correctly walks into the tail to reach small targets.
    """
    uniq, counts = np.unique(sorted_values, return_counts=True)
    cum = np.concatenate(([0], np.cumsum(counts)))
    n_values = uniq.size
    target_rows = max(1, min(target_rows, int(cum[-1])))

    best = (abs(int(counts[0]) - target_rows), 0, 0)
    end = 0
    for start in range(n_values):
        if end < start:
            end = start
        # Grow the window while doing so gets us closer to the target.
        while end + 1 < n_values:
            here = cum[end + 1] - cum[start]
            nxt = cum[end + 2] - cum[start]
            if abs(nxt - target_rows) <= abs(here - target_rows):
                end += 1
            else:
                break
        actual = int(cum[end + 1] - cum[start])
        err = abs(actual - target_rows)
        if err < best[0]:
            best = (err, start, end)
            if err == 0:
                break

    _, s, e = best
    lo, hi = int(uniq[s]), int(uniq[e])
    actual = int(cum[e + 1] - cum[s])
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

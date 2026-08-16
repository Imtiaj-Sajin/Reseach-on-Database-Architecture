"""
Derived measures.

Two quantities carry the argument of the paper.

q-error
    max(estimate, actual) / min(estimate, actual), the standard symmetric
    measure of cardinality misestimation (Moerkotte et al., VLDB 2009). A
    q-error of 1 is a perfect estimate; 100 means the planner was wrong by two
    orders of magnitude in one direction or the other. Using the ratio rather
    than a signed difference matters because a factor-of-10 underestimate and a
    factor-of-10 overestimate are equally damaging to plan choice.

regret
    time(plan the planner chose) / time(fastest plan available).
    A regret of 1.0 means the planner made the best available choice. A regret
    of 4.0 means the query took four times longer than it needed to.

    The denominator is the fastest *measured* arm, not a theoretical optimum,
    so regret is a lower bound on the true penalty. We say so explicitly
    rather than implying the oracle is perfect.
"""
from __future__ import annotations

import math

import pandas as pd

# Timing noise floor. A ratio between two sub-millisecond measurements is not
# trustworthy, so such queries are excluded from regret aggregates and the
# exclusion is reported.
#
# The test is on max(chosen, best), NOT on best alone. Filtering on best would
# throw away the most important cases in the study: when the optimal plan is a
# 0.03 ms index scan and the planner instead ran a 40 ms sequential scan, the
# best time is far below the noise floor but the regret of 1300x is entirely
# real. Screening on the larger of the two keeps those and discards only the
# cases where both plans were too fast to distinguish.
MIN_RELIABLE_MS = 1.0

# Arms in which the planner chooses freely among all available indexes. These
# are the subject of the study, never the oracle it is measured against.
PLANNER_ARMS = ("all", "all_no_brin")


def q_error(est_rows: float, actual_rows: float) -> float:
    """Symmetric cardinality estimation error.

    Both sides are floored at 1 row. PostgreSQL never estimates below 1, and an
    actual of 0 would otherwise make the ratio undefined.
    """
    e = max(float(est_rows), 1.0)
    a = max(float(actual_rows), 1.0)
    return max(e, a) / min(e, a)


def estimate_direction(est_rows: float, actual_rows: float) -> str:
    e = max(float(est_rows), 1.0)
    a = max(float(actual_rows), 1.0)
    if math.isclose(e, a, rel_tol=0.05):
        return "accurate"
    return "under" if e < a else "over"


def compute_regret(raw: pd.DataFrame) -> pd.DataFrame:
    """Join each planner-choice run against the best single-index arm.

    Input is the raw measurement table with one row per
    (dataset, arm, query, ext_stats) combination.
    """
    # Arms where every candidate index exists and the planner decides freely.
    # 'all' includes BRIN; 'all_no_brin' is the control that excludes it. Both
    # are planner-choice arms, so neither may serve as the oracle denominator.
    # Every remaining arm is a single forced access path.
    chosen = raw[raw["arm"].isin(PLANNER_ARMS)].copy()
    forced = raw[~raw["arm"].isin(PLANNER_ARMS)].copy()

    # Scale is part of the key: the same dataset and query exist at every row
    # count, and a one-million-row measurement must never be used as the
    # denominator for a ten-million-row one.
    key = ["dataset", "qid", "ext_stats"]
    if "scale" in raw.columns:
        key = ["scale"] + key

    best = (
        forced.sort_values("exec_ms_median")
        .groupby(key, as_index=False)
        .first()[key + ["arm", "exec_ms_median", "access_path", "indexes_used"]]
        .rename(
            columns={
                "arm": "best_arm",
                "exec_ms_median": "best_ms",
                "access_path": "best_access_path",
                "indexes_used": "best_indexes",
            }
        )
    )

    # many_to_one: several planner arms share one oracle row per query.
    merged = chosen.merge(best, on=key, how="inner", validate="many_to_one")
    merged["regret"] = merged["exec_ms_median"] / merged["best_ms"]
    merged["q_error"] = [
        q_error(e, a) for e, a in zip(merged["est_rows"], merged["actual_rows"])
    ]
    merged["direction"] = [
        estimate_direction(e, a) for e, a in zip(merged["est_rows"], merged["actual_rows"])
    ]
    # Absolute time surrendered, which matters alongside the ratio: a regret of
    # 3x on a 0.1 ms query is a curiosity, the same ratio on a 900 ms query is
    # an outage.
    merged["ms_lost"] = merged["exec_ms_median"] - merged["best_ms"]
    merged["reliable"] = (
        merged[["exec_ms_median", "best_ms"]].max(axis=1) >= MIN_RELIABLE_MS
    )

    # A query is "misselected" when a different arm was materially faster.
    # The 1.2 threshold keeps run-to-run noise from being reported as a
    # planner error; it is a judgement call and we state it in the paper.
    merged["misselected"] = merged["regret"] > 1.2

    return merged


def summarise(regret_df: pd.DataFrame) -> pd.DataFrame:
    """Headline table: misselection rate and regret distribution per dataset."""
    d = regret_df[regret_df["reliable"]]
    if d.empty:
        return pd.DataFrame()
    group_cols = ["dataset", "family", "arm"]
    if "scale" in d.columns:
        group_cols = ["scale"] + group_cols
    g = d.groupby(group_cols)
    return pd.DataFrame(
        {
            "n_queries": g.size(),
            "misselection_rate": g["misselected"].mean(),
            "regret_median": g["regret"].median(),
            "regret_p90": g["regret"].quantile(0.90),
            "regret_max": g["regret"].max(),
            "ms_lost_total": g["ms_lost"].sum(),
            "ms_lost_max": g["ms_lost"].max(),
            "q_error_median": g["q_error"].median(),
            "q_error_max": g["q_error"].max(),
        }
    ).reset_index()

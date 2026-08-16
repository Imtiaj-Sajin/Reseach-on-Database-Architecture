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

# Below this row count the timing noise floor dominates and ratios become
# meaningless. Queries whose fastest arm is faster than this are excluded from
# regret aggregates and the exclusion is reported.
MIN_RELIABLE_MS = 1.0


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
    # The 'all' arm is what a real deployment looks like: every candidate index
    # exists and the planner decides. Every other arm is a forced access path.
    chosen = raw[raw["arm"] == "all"].copy()
    forced = raw[raw["arm"] != "all"].copy()

    key = ["dataset", "qid", "ext_stats"]

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

    merged = chosen.merge(best, on=key, how="inner", validate="one_to_one")
    merged["regret"] = merged["exec_ms_median"] / merged["best_ms"]
    merged["q_error"] = [
        q_error(e, a) for e, a in zip(merged["est_rows"], merged["actual_rows"])
    ]
    merged["direction"] = [
        estimate_direction(e, a) for e, a in zip(merged["est_rows"], merged["actual_rows"])
    ]
    merged["reliable"] = merged["best_ms"] >= MIN_RELIABLE_MS

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
    g = d.groupby(["dataset", "family"])
    return pd.DataFrame(
        {
            "n_queries": g.size(),
            "misselection_rate": g["misselected"].mean(),
            "regret_median": g["regret"].median(),
            "regret_p90": g["regret"].quantile(0.90),
            "regret_max": g["regret"].max(),
            "q_error_median": g["q_error"].median(),
            "q_error_max": g["q_error"].max(),
        }
    ).reset_index()

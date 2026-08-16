"""
Diagnostic for the high-regret cases found in the 1M sweep.

The summary says the planner loses 13x on 1 percent selectivity range queries
while estimating cardinality almost perfectly. That rules out the obvious
explanation and means the cause has to be read out of the cost model directly,
so this script rebuilds one dataset and prints, for a single query, what the
planner costed and chose in each index arm.

Nothing here feeds the paper's numbers. It exists to establish the mechanism
before any claim is written down.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import config  # noqa: E402
import datagen  # noqa: E402
import db  # noqa: E402
import queries as qmod  # noqa: E402
from config import ALL_INDEXES_ARM, INDEX_ARMS, TARGET_SELECTIVITIES, DatasetSpec  # noqa: E402

DATA_DIR = config.ROOT / "data"
TARGET_QIDS = {"tsrange_0.01", "range_0.01", "tsrange_0.05", "range_0.05"}


def explain_costs(conn, sql: str) -> dict:
    """Plan the query without executing it, and report what each option cost."""
    with conn.cursor() as cur:
        cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
        payload = cur.fetchone()[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
    plan = payload[0]["Plan"]
    return db.summarise_plan(plan)


def main() -> int:
    spec = DatasetSpec(
        name="diag", n_rows=1_000_000, zipf_s=0.0, dep_strength=0.0, physical_corr=0.5
    )
    ds = datagen.generate(spec, DATA_DIR)
    qs = [q for q in qmod.build_queries(ds, TARGET_SELECTIVITIES) if q.qid in TARGET_QIDS]

    with db.session() as conn:
        db.create_table(conn, spec.table)
        db.load_csv(conn, spec.table, ds.csv_path)
        db.analyze(conn, spec.table)

        for q in qs:
            print(f"\n{'='*100}")
            print(f"{q.qid}   true_rows={q.true_rows}  selectivity={q.achieved_selectivity:.4f}")
            print(f"  {q.sql}")
            print(f"{'='*100}")
            print(
                f"  {'arm':22s} {'chosen':14s} {'index used':28s} "
                f"{'est':>9s} {'cost':>11s} {'actual ms':>10s}"
            )

            for arm in list(INDEX_ARMS) + [ALL_INDEXES_ARM]:
                if q.family not in arm.supports:
                    continue
                db.apply_index_arm(conn, spec.table, arm.ddl, prefix=f"ix_{spec.name}")
                planned = explain_costs(conn, q.sql)
                measured = db.measure(conn, q.sql, repeats=3, warmup=1)
                print(
                    f"  {arm.name:22s} {planned['access_path']:14s} "
                    f"{','.join(planned['indexes_used'])[:28]:28s} "
                    f"{planned['est_rows']:9.0f} {planned['total_cost']:11.1f} "
                    f"{measured['exec_ms_median']:10.2f}"
                )

            # With every index available, force the sequential scan off and see
            # what the planner would have picked next, and what it would cost.
            db.apply_index_arm(conn, spec.table, ALL_INDEXES_ARM.ddl, prefix=f"ix_{spec.name}")
            with conn.cursor() as cur:
                cur.execute("SET enable_seqscan = off")
            planned = explain_costs(conn, q.sql)
            measured = db.measure(conn, q.sql, repeats=3, warmup=1)
            print(
                f"  {'all (seqscan off)':22s} {planned['access_path']:14s} "
                f"{','.join(planned['indexes_used'])[:28]:28s} "
                f"{planned['est_rows']:9.0f} {planned['total_cost']:11.1f} "
                f"{measured['exec_ms_median']:10.2f}"
            )
            with conn.cursor() as cur:
                cur.execute("SET enable_seqscan = on")

            # random_page_cost sweep on the full index set. The default of 4.0
            # assumes a random page costs four times a sequential one, which is
            # a spinning-disk assumption.
            print(f"  {'-'*90}")
            for rpc in (4.0, 2.0, 1.5, 1.1, 1.0):
                with conn.cursor() as cur:
                    cur.execute("SET random_page_cost = %s", (rpc,))
                planned = explain_costs(conn, q.sql)
                measured = db.measure(conn, q.sql, repeats=3, warmup=1)
                print(
                    f"  random_page_cost={rpc:<5} {planned['access_path']:14s} "
                    f"{','.join(planned['indexes_used'])[:28]:28s} "
                    f"{planned['est_rows']:9.0f} {planned['total_cost']:11.1f} "
                    f"{measured['exec_ms_median']:10.2f}"
                )
            with conn.cursor() as cur:
                cur.execute("SET random_page_cost = 4.0")

        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {spec.table} CASCADE")

    try:
        ds.csv_path.unlink(missing_ok=True)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

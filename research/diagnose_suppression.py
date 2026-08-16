"""
Hypothesis: the presence of a BRIN index suppresses the B-tree bitmap path.

Evidence so far. With both indexes present and identical statistics, the
planner chose a plan it costed at 29304 while a B-tree bitmap plan it costed at
13917 existed. A cost-based optimiser does not knowingly pick a path it rates
as twice as expensive, so the likely explanation is that the B-tree bitmap path
is discarded during path generation and never reaches the final comparison.

Test: force the planner into a bitmap plan by disabling every other access
method, with both indexes present. If it still reaches for BRIN when the B-tree
bitmap path is cheaper by its own cost model, the path is being suppressed
rather than rejected on cost.

The `single index` columns are the control: the same query with the other index
absent, which shows what the suppressed path would have cost and run at.
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
from config import TARGET_SELECTIVITIES, DatasetSpec  # noqa: E402

DATA_DIR = config.ROOT / "data"
CORRS = (0.0, 0.5, 0.95, 1.0)
QIDS = ("tsrange_0.001", "tsrange_0.01", "tsrange_0.05", "tsrange_0.1")

FORCE_BITMAP = (
    "SET enable_seqscan = off",
    "SET enable_indexscan = off",
    "SET enable_indexonlyscan = off",
    "SET enable_bitmapscan = on",
)
RESET = (
    "SET enable_seqscan = on",
    "SET enable_indexscan = on",
    "SET enable_indexonlyscan = on",
    "SET enable_bitmapscan = on",
)


def plan_of(conn, sql: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
        p = cur.fetchone()[0]
        if isinstance(p, str):
            p = json.loads(p)
    return db.summarise_plan(p[0]["Plan"])


def apply(conn, stmts) -> None:
    with conn.cursor() as cur:
        for s in stmts:
            cur.execute(s)


def main() -> int:
    print(
        f"{'corr':>5} {'query':>15} | {'BOTH indexes, bitmap forced':>34} {'cost':>8} {'ms':>7}"
        f" | {'BTREE only, bitmap forced':>30} {'cost':>8} {'ms':>7} | {'suppressed?':>11}"
    )
    print("-" * 140)

    suppressed_count = 0
    total = 0

    for corr in CORRS:
        spec = DatasetSpec(
            name="supp", n_rows=1_000_000, zipf_s=0.0, dep_strength=0.0, physical_corr=corr
        )
        ds = datagen.generate(spec, DATA_DIR)
        qs = [q for q in qmod.build_queries(ds, TARGET_SELECTIVITIES) if q.qid in QIDS]

        with db.session() as conn:
            db.create_table(conn, spec.table)
            db.load_csv(conn, spec.table, ds.csv_path)
            db.drop_all_indexes(conn, spec.table)
            with conn.cursor() as cur:
                cur.execute(f"CREATE INDEX ix_supp_btree ON {spec.table} USING btree (ts)")
                cur.execute(f"CREATE INDEX ix_supp_brin ON {spec.table} USING brin (ts)")
            db.analyze(conn, spec.table)  # once, and never again

            for q in qs:
                apply(conn, FORCE_BITMAP)
                both = plan_of(conn, q.sql)
                both_m = db.measure(conn, q.sql, repeats=5, warmup=1)
                apply(conn, RESET)

                # Control: same statistics, BRIN removed.
                with conn.cursor() as cur:
                    cur.execute("DROP INDEX IF EXISTS ix_supp_brin")
                apply(conn, FORCE_BITMAP)
                bt = plan_of(conn, q.sql)
                bt_m = db.measure(conn, q.sql, repeats=5, warmup=1)
                apply(conn, RESET)
                with conn.cursor() as cur:
                    cur.execute(f"CREATE INDEX ix_supp_brin ON {spec.table} USING brin (ts)")

                both_ix = ",".join(both["indexes_used"])[:22]
                bt_ix = ",".join(bt["indexes_used"])[:20]

                # Suppression means: with both indexes the planner used BRIN,
                # while the B-tree path it did not use was cheaper by its own
                # cost model.
                used_brin = "brin" in both_ix
                cheaper_available = bt["total_cost"] < both["total_cost"] * 0.99
                flag = "YES" if (used_brin and cheaper_available) else ""
                if flag:
                    suppressed_count += 1
                total += 1

                print(
                    f"{corr:>5} {q.qid:>15} | {both['access_path']+'/'+both_ix:>34} "
                    f"{both['total_cost']:>8.0f} {both_m['exec_ms_median']:>7.2f}"
                    f" | {bt['access_path']+'/'+bt_ix:>30} {bt['total_cost']:>8.0f} "
                    f"{bt_m['exec_ms_median']:>7.2f} | {flag:>11}"
                )

            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {spec.table} CASCADE")

        try:
            ds.csv_path.unlink(missing_ok=True)
        except OSError:
            pass

    print(
        f"\nBRIN chosen while a cheaper B-tree bitmap path existed: "
        f"{suppressed_count}/{total} cases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

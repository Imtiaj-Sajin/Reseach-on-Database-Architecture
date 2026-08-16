"""
Is the BRIN-versus-B-tree choice a genuine preference, or a near-tie decided by
ANALYZE sampling noise?

The arm sweep re-runs ANALYZE for every index configuration, so the two
configurations being compared had slightly different row estimates. That is
enough to flip a decision when the two costs sit within a few percent of each
other, and it would be wrong to report "the planner prefers BRIN" if the real
finding is "the planner cannot tell these apart".

This holds the statistics fixed: build both indexes, ANALYZE once, then drop one
index WITHOUT re-analyzing, so the only thing that changes between the two
EXPLAINs is which access paths exist.
"""
from __future__ import annotations

import statistics
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


def plan_of(conn, sql: str) -> dict:
    import json

    with conn.cursor() as cur:
        cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
        p = cur.fetchone()[0]
        if isinstance(p, str):
            p = json.loads(p)
    return db.summarise_plan(p[0]["Plan"])


def main() -> int:
    print(
        f"{'phys_corr':>9} {'pg_corr':>8} | {'both: chosen':>26} {'cost':>9} {'ms':>8} "
        f"| {'btree only: chosen':>22} {'cost':>9} {'ms':>8} | {'cost gap':>9} {'speed gap':>9}"
    )
    print("-" * 130)

    for corr in CORRS:
        spec = DatasetSpec(
            name="tie", n_rows=1_000_000, zipf_s=0.0, dep_strength=0.0, physical_corr=corr
        )
        ds = datagen.generate(spec, DATA_DIR)
        q = next(
            q for q in qmod.build_queries(ds, TARGET_SELECTIVITIES) if q.qid == "tsrange_0.01"
        )

        with db.session() as conn:
            db.create_table(conn, spec.table)
            db.load_csv(conn, spec.table, ds.csv_path)

            db.drop_all_indexes(conn, spec.table)
            with conn.cursor() as cur:
                cur.execute(f"CREATE INDEX ix_tie_btree ON {spec.table} USING btree (ts)")
                cur.execute(f"CREATE INDEX ix_tie_brin ON {spec.table} USING brin (ts)")
            # ANALYZE exactly once. Everything below sees identical statistics.
            db.analyze(conn, spec.table)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT correlation FROM pg_stats WHERE tablename=%s AND attname='ts'",
                    (spec.table,),
                )
                pg_corr = cur.fetchone()[0]

            both = plan_of(conn, q.sql)
            both_m = db.measure(conn, q.sql, repeats=5, warmup=1)

            # Drop BRIN. No ANALYZE, so statistics are byte-identical.
            with conn.cursor() as cur:
                cur.execute("DROP INDEX ix_tie_brin")

            btree = plan_of(conn, q.sql)
            btree_m = db.measure(conn, q.sql, repeats=5, warmup=1)

            chosen_both = f"{both['access_path']}/{','.join(both['indexes_used'])[:14]}"
            chosen_bt = f"{btree['access_path']}/{','.join(btree['indexes_used'])[:12]}"
            cost_gap = both["total_cost"] / max(btree["total_cost"], 1e-9)
            speed_gap = both_m["exec_ms_median"] / max(btree_m["exec_ms_median"], 1e-9)

            print(
                f"{corr:>9} {pg_corr:>8.3f} | {chosen_both:>26} "
                f"{both['total_cost']:>9.0f} {both_m['exec_ms_median']:>8.2f} "
                f"| {chosen_bt:>22} {btree['total_cost']:>9.0f} {btree_m['exec_ms_median']:>8.2f} "
                f"| {cost_gap:>8.2f}x {speed_gap:>8.2f}x"
            )

            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {spec.table} CASCADE")

        try:
            ds.csv_path.unlink(missing_ok=True)
        except OSError:
            pass

    print(
        "\ncost gap  = planner's cost with both indexes / cost with only the B-tree"
        "\nspeed gap = measured time with both indexes / time with only the B-tree"
        "\n\nA cost gap near 1.0 alongside a large speed gap means the cost model"
        "\nrates the two plans as equivalent while they are not."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

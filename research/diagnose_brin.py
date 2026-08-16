"""
Why does the planner choose BRIN over a cheaper B-tree path?

The arm sweep reported the all-index plan costing 14852 while the B-tree-only
plan costed 14272. A cheaper path losing is not something the planner does, so
either the two costs are not comparable or the plan summariser is misreading
the tree. This prints the full plan text in each configuration so the question
is settled by reading PostgreSQL's own output rather than my summary of it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import config  # noqa: E402
import datagen  # noqa: E402
import db  # noqa: E402
import queries as qmod  # noqa: E402
from config import ALL_INDEXES_ARM, TARGET_SELECTIVITIES, DatasetSpec  # noqa: E402

DATA_DIR = config.ROOT / "data"


def explain_text(conn, sql: str, analyze: bool = False) -> str:
    opt = "ANALYZE, BUFFERS, TIMING OFF, " if analyze else ""
    with conn.cursor() as cur:
        cur.execute(f"EXPLAIN ({opt}COSTS ON) {sql}")
        return "\n".join("      " + r[0] for r in cur.fetchall())


def main() -> int:
    spec = DatasetSpec(
        name="brindiag", n_rows=1_000_000, zipf_s=0.0, dep_strength=0.0, physical_corr=0.5
    )
    ds = datagen.generate(spec, DATA_DIR)
    q = next(q for q in qmod.build_queries(ds, TARGET_SELECTIVITIES) if q.qid == "tsrange_0.01")

    with db.session() as conn:
        db.create_table(conn, spec.table)
        db.load_csv(conn, spec.table, ds.csv_path)
        db.analyze(conn, spec.table)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT attname, correlation FROM pg_stats "
                "WHERE tablename = %s AND attname = 'ts'",
                (spec.table,),
            )
            row = cur.fetchone()
        print(f"designed physical_corr = {spec.physical_corr}")
        print(f"generator realised rank corr = {ds.realised_ts_rank_corr:.4f}")
        print(f"pg_stats correlation for ts = {row[1] if row else 'n/a'}")
        print(f"\nquery: {q.sql}\ntrue rows: {q.true_rows}\n")

        configs = [
            ("BOTH indexes (btree_ts + brin_ts)", (
                "CREATE INDEX {ix}_btree_ts ON {t} USING btree (ts)",
                "CREATE INDEX {ix}_brin_ts ON {t} USING brin (ts)",
            )),
            ("btree_ts ONLY", ("CREATE INDEX {ix}_btree_ts ON {t} USING btree (ts)",)),
            ("brin_ts ONLY", ("CREATE INDEX {ix}_brin_ts ON {t} USING brin (ts)",)),
            ("FULL all-index arm", ALL_INDEXES_ARM.ddl),
        ]

        for label, ddl in configs:
            db.apply_index_arm(conn, spec.table, ddl, prefix=f"ix_{spec.name}")
            print("=" * 96)
            print(label)
            print("=" * 96)
            print("  -- plan as chosen --")
            print(explain_text(conn, q.sql, analyze=True))

            # Force the B-tree path so its true cost is visible for comparison.
            if "btree" in " ".join(ddl):
                with conn.cursor() as cur:
                    cur.execute("SET enable_seqscan = off")
                    cur.execute("SET enable_bitmapscan = off")
                print("  -- forced index scan (seqscan and bitmapscan disabled) --")
                print(explain_text(conn, q.sql, analyze=True))
                with conn.cursor() as cur:
                    cur.execute("SET enable_seqscan = on")
                    cur.execute("SET enable_bitmapscan = on")
            print()

        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {spec.table} CASCADE")

    try:
        ds.csv_path.unlink(missing_ok=True)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Completing the extended-statistics mechanism.

Established so far: with a corrected estimate the planner abandons the
composite (a, b) index for a BitmapAnd over two single-column indexes, and the
result is slower. The remaining question is whether the planner is behaving
rationally by its own model, that is, whether the composite path really does
cost more once the estimate is correct.

Test: with extended statistics in place, drop the single-column indexes so the
composite path is the only one available, and read its cost. If that cost
exceeds the BitmapAnd cost, the planner made a defensible choice on a cost
model that misprices the composite index. If it is lower, the planner failed to
consider a cheaper path, which is a different and more serious defect.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import config  # noqa: E402
import datagen  # noqa: E402
import db  # noqa: E402
import queries as qmod  # noqa: E402
from config import EXTENDED_STATS_DDL, TARGET_SELECTIVITIES, DatasetSpec  # noqa: E402

DATA_DIR = config.ROOT / "data"


def explain_text(conn, sql: str) -> str:
    with conn.cursor() as cur:
        cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, COSTS ON) {sql}")
        return "\n".join("      " + r[0] for r in cur.fetchall())


def main() -> int:
    spec = DatasetSpec(
        name="comp", n_rows=1_000_000, zipf_s=0.0, dep_strength=1.0, physical_corr=0.0
    )
    ds = datagen.generate(spec, DATA_DIR)
    qs = [q for q in qmod.build_queries(ds, TARGET_SELECTIVITIES)
          if q.family == "conj" and q.params.get("variant") == "dependent"]
    q = max(qs, key=lambda x: x.true_rows)

    with db.session() as conn:
        db.create_table(conn, spec.table)
        db.load_csv(conn, spec.table, ds.csv_path)

        print(f"query: {q.sql}\ntrue rows: {q.true_rows}\n")

        configs = [
            ("all three indexes, WITH extstats", True,
             ("CREATE INDEX {ix}_a ON {t} USING btree (a)",
              "CREATE INDEX {ix}_b ON {t} USING btree (b)",
              "CREATE INDEX {ix}_ab ON {t} USING btree (a, b)")),
            ("composite ONLY, WITH extstats", True,
             ("CREATE INDEX {ix}_ab ON {t} USING btree (a, b)",)),
            ("separate ONLY, WITH extstats", True,
             ("CREATE INDEX {ix}_a ON {t} USING btree (a)",
              "CREATE INDEX {ix}_b ON {t} USING btree (b)")),
            ("all three indexes, WITHOUT extstats", False,
             ("CREATE INDEX {ix}_a ON {t} USING btree (a)",
              "CREATE INDEX {ix}_b ON {t} USING btree (b)",
              "CREATE INDEX {ix}_ab ON {t} USING btree (a, b)")),
            ("composite ONLY, WITHOUT extstats", False,
             ("CREATE INDEX {ix}_ab ON {t} USING btree (a, b)",)),
        ]

        for label, ext, ddl in configs:
            db.apply_index_arm(conn, spec.table, ddl, prefix="ix_comp")
            if ext:
                db.create_extended_stats(conn, spec.table, EXTENDED_STATS_DDL, name="st_comp")
            else:
                db.drop_extended_stats(conn, spec.table)
                db.analyze(conn, spec.table)

            m = db.measure(conn, q.sql, repeats=9, warmup=2)
            print("=" * 96)
            print(f"{label}")
            print("=" * 96)
            print(explain_text(conn, q.sql))
            print(
                f"      >> median {m['exec_ms_median']:.3f} ms   "
                f"est={m['est_rows']:.0f} actual={m['actual_rows']:.0f}   "
                f"total_cost={m['total_cost']:.2f}\n"
            )

        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {spec.table} CASCADE")

    try:
        ds.csv_path.unlink(missing_ok=True)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

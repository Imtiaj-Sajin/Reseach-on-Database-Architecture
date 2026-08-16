"""
Extended statistics fix the cardinality estimate and make the query slower.

That is counterintuitive enough that it needs to be read off the plans rather
than inferred from aggregates. This script builds one strongly dependent
dataset and prints, for the same query, the full plan with and without
CREATE STATISTICS, plus repeated timings so the difference can be separated
from noise.

Everything else is held constant: same table, same data, same indexes, same
session. Only the extended statistics object is added or removed.
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
from config import (  # noqa: E402
    ALL_INDEXES_ARM,
    EXTENDED_STATS_DDL,
    TARGET_SELECTIVITIES,
    DatasetSpec,
)

DATA_DIR = config.ROOT / "data"
REPEATS = 15


def explain_text(conn, sql: str) -> str:
    with conn.cursor() as cur:
        cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, COSTS ON) {sql}")
        return "\n".join("      " + r[0] for r in cur.fetchall())


def timings(conn, sql: str, n: int = REPEATS) -> list[float]:
    out = []
    m = db.measure(conn, sql, repeats=n, warmup=2)
    return m["runs"]


def main() -> int:
    for dep in (0.5, 1.0):
        spec = DatasetSpec(
            name="ext", n_rows=1_000_000, zipf_s=0.0, dep_strength=dep, physical_corr=0.0
        )
        ds = datagen.generate(spec, DATA_DIR)
        qs = [q for q in qmod.build_queries(ds, TARGET_SELECTIVITIES)
              if q.family == "conj" and q.params.get("variant") == "dependent"]
        q = max(qs, key=lambda x: x.true_rows)

        with db.session() as conn:
            db.create_table(conn, spec.table)
            db.load_csv(conn, spec.table, ds.csv_path)
            db.apply_index_arm(conn, spec.table, ALL_INDEXES_ARM.ddl, prefix="ix_ext")

            print("=" * 100)
            print(f"dep_strength = {dep}   query: {q.sql}")
            print(f"true rows = {q.true_rows}")
            print("=" * 100)

            for label in ("WITHOUT extended statistics", "WITH extended statistics"):
                if label.startswith("WITHOUT"):
                    db.drop_extended_stats(conn, spec.table)
                    db.analyze(conn, spec.table)
                else:
                    db.create_extended_stats(
                        conn, spec.table, EXTENDED_STATS_DDL, name="st_ext"
                    )

                runs = timings(conn, q.sql)
                print(f"\n--- {label} ---")
                print(explain_text(conn, q.sql))
                print(
                    f"      >> {REPEATS} runs: median={statistics.median(runs):.3f} ms  "
                    f"mean={statistics.fmean(runs):.3f}  min={min(runs):.3f}  "
                    f"max={max(runs):.3f}  stdev={statistics.stdev(runs):.3f}"
                )

            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {spec.table} CASCADE")

        try:
            ds.csv_path.unlink(missing_ok=True)
        except OSError:
            pass
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

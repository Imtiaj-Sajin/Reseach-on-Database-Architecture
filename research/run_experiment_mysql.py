"""
MariaDB arm of the access-path selection study.

Runs the same datasets, the same queries and the same regret metric as
run_experiment.py, against MariaDB instead of PostgreSQL, so the two can be
compared directly.

Usage
-----
    python run_experiment_mysql.py --pilot
    python run_experiment_mysql.py --rows 1000000

Output goes to results/raw/mysql_measurements_<tag>.jsonl with the same record
schema as the PostgreSQL sweeps, plus a `dbms` field so the two can be loaded
into one table.

Index arms differ from PostgreSQL by necessity, not by choice: InnoDB offers
neither BRIN nor hash indexes. The arms retained are exactly those with a
PostgreSQL counterpart, so every cross-system comparison is like for like.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import config  # noqa: E402
from config import RAW_DIR, REPEATS, TARGET_SELECTIVITIES, WARMUP_RUNS, IndexArm  # noqa: E402
from config import build_dataset_grid, DatasetSpec  # noqa: E402
import datagen  # noqa: E402
import db_mysql as db  # noqa: E402
import queries as qmod  # noqa: E402

DATA_DIR = config.ROOT / "data"

# Arms with a direct PostgreSQL counterpart. BRIN and hash are absent from
# InnoDB, so those PostgreSQL arms have no analogue and are omitted rather
# than approximated.
MYSQL_ARMS: list[IndexArm] = [
    IndexArm(name="none", ddl=(), supports=("eq", "range", "conj", "ts_range")),
    IndexArm(
        name="btree_skew",
        ddl=("CREATE INDEX {ix}_btree_skew ON {t} (k_skew)",),
        supports=("eq", "range"),
    ),
    IndexArm(
        name="btree_a_b_separate",
        ddl=(
            "CREATE INDEX {ix}_a ON {t} (a)",
            "CREATE INDEX {ix}_b ON {t} (b)",
        ),
        supports=("conj",),
    ),
    IndexArm(
        name="btree_ab_composite",
        ddl=("CREATE INDEX {ix}_ab ON {t} (a, b)",),
        supports=("conj",),
    ),
    IndexArm(
        name="btree_ts",
        ddl=("CREATE INDEX {ix}_btree_ts ON {t} (ts)",),
        supports=("ts_range",),
    ),
]

# The free-choice arm: every index available, optimiser decides. This is the
# counterpart of PostgreSQL's all_no_brin, since neither contains BRIN.
MYSQL_ALL_ARM = IndexArm(
    name="all_no_brin",
    ddl=(
        "CREATE INDEX {ix}_btree_skew ON {t} (k_skew)",
        "CREATE INDEX {ix}_a ON {t} (a)",
        "CREATE INDEX {ix}_b ON {t} (b)",
        "CREATE INDEX {ix}_ab ON {t} (a, b)",
        "CREATE INDEX {ix}_btree_ts ON {t} (ts)",
    ),
    supports=("eq", "range", "conj", "ts_range"),
)

RAW_PATH = RAW_DIR / "mysql_measurements.jsonl"
META_PATH = RAW_DIR / "mysql_run_metadata.json"


def set_output_paths(tag: str) -> None:
    global RAW_PATH, META_PATH
    RAW_PATH = RAW_DIR / f"mysql_measurements_{tag}.jsonl"
    META_PATH = RAW_DIR / f"mysql_run_metadata_{tag}.json"


def load_done_keys() -> set[tuple]:
    done: set[tuple] = set()
    if not RAW_PATH.exists():
        return done
    with RAW_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                done.add((r["dataset"], r["arm"], r["qid"]))
    return done


def append_record(rec: dict) -> None:
    with RAW_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def run_arm(conn, spec, arm, query_set, done, dataset_meta) -> int:
    pending = [
        q for q in query_set
        if q.family in arm.supports and (spec.name, arm.name, q.qid) not in done
    ]
    if not pending:
        return 0

    info = db.apply_index_arm(conn, spec.table, arm.ddl, prefix=f"ix_{spec.name}")

    n = 0
    for q in pending:
        t_start = time.time()
        m = db.measure(conn, q.sql, repeats=REPEATS, warmup=WARMUP_RUNS)
        t_end = time.time()
        rec = {
            # Wall-clock stamps. Without these, total elapsed time cannot be
            # reconstructed from the results: the per-query timings capture
            # only execution, omitting ANALYZE, planning overhead and any
            # idle gaps. Recording them makes the run itself measurable, which
            # matters here because execution cost differs by an order of
            # magnitude between the two systems.
            "measured_at": t_start,
            "measured_wall_seconds": t_end - t_start,
            "dbms": "mariadb",
            "dataset": spec.name,
            "dataset_spec": spec.to_dict(),
            "dataset_meta": dataset_meta,
            "arm": arm.name,
            "arm_index_bytes": info["index_bytes"],
            "arm_build_seconds": info["build_seconds"],
            "ext_stats": False,
            **q.to_dict(),
            **m,
        }
        append_record(rec)
        done.add((spec.name, arm.name, q.qid))
        n += 1
        print(
            f"    {arm.name:20s} {q.qid:22s} {m['access_path']:12s} "
            f"({m['raw_access_type']:11s}) {m['exec_ms_median']:9.2f} ms "
            f"est={m['est_rows']:>10.0f} act={m['actual_rows']:>10.0f}",
            flush=True,
        )
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--only", type=str, default=None)
    ap.add_argument("--tag", type=str, default=None)
    args = ap.parse_args()

    n_rows = 50_000 if args.pilot else args.rows
    tag = args.tag or ("pilot" if args.pilot else
                       f"{n_rows // 1_000_000}m" if n_rows >= 1_000_000
                       else f"{n_rows // 1000}k")
    set_output_paths(tag)
    print(f"Output: {RAW_PATH.name}")

    specs = build_dataset_grid(n_rows)
    if args.pilot:
        specs = [s for s in specs if s.name in ("baseline", "dep10", "skew10")]
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        specs = [s for s in specs if s.name in wanted]
    print(f"Datasets: {[s.name for s in specs]}  rows={n_rows}")

    done = load_done_keys()
    print(f"Already measured: {len(done)}")

    with db.session() as conn:
        meta = {
            "dbms": "mariadb",
            "server": db.server_metadata(conn),
            "client": {"python": platform.python_version(), "platform": platform.platform()},
            "protocol": {
                "repeats": REPEATS,
                "warmup_runs": WARMUP_RUNS,
                "instrument": "ANALYZE FORMAT=JSON",
                "statistic": "median of repeats after discarding warmup",
                "cache_state": "warm",
            },
            "n_rows": n_rows,
        }
        META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"Server: {meta['server']['version']}  "
              f"buffer_pool={meta['server'].get('innodb_buffer_pool_size')}")

        for spec in specs:
            print(f"\n=== dataset {spec.name} ===", flush=True)
            t0 = time.perf_counter()
            ds = datagen.generate(spec, DATA_DIR)
            print(f"  generated in {time.perf_counter()-t0:.1f}s")

            db.create_table(conn, spec.table)
            t0 = time.perf_counter()
            db.load_csv(conn, spec.table, ds.csv_path)
            db.analyze(conn, spec.table)
            print(f"  loaded in {time.perf_counter()-t0:.1f}s")

            dataset_meta = {
                "realised_ts_rank_corr": ds.realised_ts_rank_corr,
                "table_stats": db.table_stats(conn, spec.table),
            }

            query_set = qmod.build_queries(ds, TARGET_SELECTIVITIES)
            print(f"  {len(query_set)} queries")

            total = 0
            for arm in MYSQL_ARMS:
                total += run_arm(conn, spec, arm, query_set, done, dataset_meta)
            total += run_arm(conn, spec, MYSQL_ALL_ARM, query_set, done, dataset_meta)
            print(f"  {total} new measurements")

            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {spec.table}")
            try:
                ds.csv_path.unlink(missing_ok=True)
            except OSError:
                pass

    print(f"\nDone -> {RAW_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

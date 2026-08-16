"""
Main experiment driver.

Usage
-----
    python run_experiment.py --pilot          quick smoke run, 50k rows, 2 datasets
    python run_experiment.py --rows 1000000   full sweep at one million rows

Results are appended to results/raw/measurements.jsonl, one JSON object per
measured (dataset, index arm, query, extended-stats state) combination. The run
is resumable: anything already present in that file is skipped, so an
interrupted sweep can be restarted without losing work.
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
from config import (  # noqa: E402
    ALL_INDEXES_ARM,
    ALL_NO_BRIN_ARM,
    EXTENDED_STATS_DDL,
    INDEX_ARMS,
    RAW_DIR,
    REPEATS,
    TARGET_SELECTIVITIES,
    WARMUP_RUNS,
    build_dataset_grid,
    DatasetSpec,
)
import datagen  # noqa: E402
import db  # noqa: E402
import queries as qmod  # noqa: E402

# Set per-run by main(), because the resume key does not include the row count
# and a ten-million-row sweep must not be skipped on the strength of
# one-million-row results already sitting in the file.
RAW_PATH = RAW_DIR / "measurements.jsonl"
META_PATH = RAW_DIR / "run_metadata.json"
DATA_DIR = config.ROOT / "data"


def set_output_paths(tag: str) -> None:
    global RAW_PATH, META_PATH
    RAW_PATH = RAW_DIR / f"measurements_{tag}.jsonl"
    META_PATH = RAW_DIR / f"run_metadata_{tag}.json"


def completed_path() -> Path:
    return RAW_PATH.with_name(RAW_PATH.stem + "_completed.json")


def load_completed_datasets() -> dict[str, int]:
    """Datasets fully measured at a given row count, from a previous run."""
    p = completed_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def mark_dataset_complete(name: str, n_rows: int) -> None:
    done = load_completed_datasets()
    done[name] = n_rows
    completed_path().write_text(json.dumps(done, indent=2), encoding="utf-8")


def load_done_keys() -> set[tuple]:
    """Keys already measured, so a restart skips them."""
    done: set[tuple] = set()
    if not RAW_PATH.exists():
        return done
    with RAW_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            done.add((r["dataset"], r["arm"], r["qid"], r["ext_stats"]))
    return done


def append_record(rec: dict) -> None:
    with RAW_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def run_arm(
    conn,
    spec: DatasetSpec,
    arm_name: str,
    arm_ddl: tuple[str, ...],
    supports: tuple[str, ...],
    query_set,
    ext_stats: bool,
    done: set[tuple],
    dataset_meta: dict,
) -> int:
    """Build one index arm and measure every query it can serve."""
    pending = [
        q
        for q in query_set
        if q.family in supports and (spec.name, arm_name, q.qid, ext_stats) not in done
    ]
    if not pending:
        return 0

    t0 = time.perf_counter()
    arm_info = db.apply_index_arm(conn, spec.table, arm_ddl, prefix=f"ix_{spec.name}")
    build_s = time.perf_counter() - t0

    if ext_stats:
        db.create_extended_stats(
            conn, spec.table, EXTENDED_STATS_DDL, name=f"st_{spec.name}"
        )
    else:
        db.drop_extended_stats(conn, spec.table)
        db.analyze(conn, spec.table)

    n = 0
    for q in pending:
        m = db.measure(conn, q.sql, repeats=REPEATS, warmup=WARMUP_RUNS)
        rec = {
            "dataset": spec.name,
            "dataset_spec": spec.to_dict(),
            "dataset_meta": dataset_meta,
            "arm": arm_name,
            "arm_index_bytes": arm_info["index_bytes"],
            "arm_build_seconds": build_s,
            "ext_stats": ext_stats,
            **q.to_dict(),
            **m,
        }
        append_record(rec)
        done.add((spec.name, arm_name, q.qid, ext_stats))
        n += 1
        print(
            f"    {arm_name:20s} ext={int(ext_stats)} {q.qid:22s} "
            f"{m['access_path']:14s} {m['exec_ms_median']:9.2f} ms "
            f"est={m['est_rows']:>10.0f} act={m['actual_rows']:>10.0f}",
            flush=True,
        )
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--pilot", action="store_true", help="tiny run to validate the pipeline")
    ap.add_argument("--only", type=str, default=None, help="comma separated dataset names")
    ap.add_argument(
        "--tag",
        type=str,
        default=None,
        help="output file suffix; defaults to the row count, e.g. 1m or 10m",
    )
    args = ap.parse_args()

    n_rows = 50_000 if args.pilot else args.rows
    tag = args.tag or ("pilot" if args.pilot else f"{n_rows // 1_000_000}m" if n_rows >= 1_000_000 else f"{n_rows // 1000}k")
    set_output_paths(tag)
    print(f"Output tag: {tag}  ->  {RAW_PATH.name}")

    specs = build_dataset_grid(n_rows)
    if args.pilot:
        specs = [s for s in specs if s.name in ("baseline", "dep10", "phys10")]
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        specs = [s for s in specs if s.name in wanted]

    print(f"Datasets: {[s.name for s in specs]}  rows={n_rows}")

    db.ensure_database()
    done = load_done_keys()
    print(f"Already measured: {len(done)} records")

    with db.session() as conn:
        meta = {
            "server": db.server_metadata(conn),
            "client": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "processor": platform.processor(),
            },
            "protocol": {
                "repeats": REPEATS,
                "warmup_runs": WARMUP_RUNS,
                "instrument": "EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON)",
                "statistic": "median of repeats after discarding warmup",
                "cache_state": "warm",
            },
            "n_rows": n_rows,
        }
        META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"Server: {meta['server']['version']}")

        completed = load_completed_datasets()

        for spec in specs:
            print(f"\n=== dataset {spec.name} ===", flush=True)

            # Skip fully measured datasets before doing any work. Generating and
            # loading a ten-million-row table takes minutes, and on a resume
            # that cost was previously paid for every already-finished dataset
            # only to discover there was nothing left to measure. The query
            # identifiers depend on the generated data, so completion cannot be
            # inferred from the measurement file without generating first;
            # hence an explicit marker written when a dataset finishes.
            if completed.get(spec.name) == n_rows:
                print("  already complete, skipping")
                continue

            csv_path = DATA_DIR / f"{spec.table}.csv"
            t0 = time.perf_counter()
            ds = datagen.generate(spec, DATA_DIR)
            print(f"  generated in {time.perf_counter()-t0:.1f}s -> {ds.csv_path.name}")

            db.create_table(conn, spec.table)
            t0 = time.perf_counter()
            db.load_csv(conn, spec.table, ds.csv_path)
            db.analyze(conn, spec.table)
            print(f"  loaded in {time.perf_counter()-t0:.1f}s")

            stats = db.table_stats(conn, spec.table)
            dataset_meta = {
                "realised_ts_rank_corr": ds.realised_ts_rank_corr,
                "pg_stats": stats,
            }
            pg_ts_corr = stats["columns"].get("ts", {}).get("correlation")
            print(
                f"  ts correlation: designed={spec.physical_corr} "
                f"realised={ds.realised_ts_rank_corr:.4f} pg_stats={pg_ts_corr}"
            )

            query_set = qmod.build_queries(ds, TARGET_SELECTIVITIES)
            print(f"  {len(query_set)} queries")

            total = 0
            for arm in INDEX_ARMS:
                total += run_arm(
                    conn, spec, arm.name, arm.ddl, arm.supports,
                    query_set, False, done, dataset_meta,
                )
            for all_arm in (ALL_INDEXES_ARM, ALL_NO_BRIN_ARM):
                total += run_arm(
                    conn, spec, all_arm.name, all_arm.ddl,
                    all_arm.supports, query_set, False, done, dataset_meta,
                )

            # Extended statistics arm. Only the conjunctive family can be
            # affected, since extended statistics describe multi-column
            # dependencies, so we re-measure just that family.
            conj_only = [q for q in query_set if q.family == "conj"]
            if conj_only:
                total += run_arm(
                    conn, spec, ALL_INDEXES_ARM.name, ALL_INDEXES_ARM.ddl,
                    ("conj",), conj_only, True, done, dataset_meta,
                )
                for arm in INDEX_ARMS:
                    if "conj" in arm.supports:
                        total += run_arm(
                            conn, spec, arm.name, arm.ddl, ("conj",),
                            conj_only, True, done, dataset_meta,
                        )

            print(f"  {total} new measurements")

            # The CSV is large and fully regenerable from the seed.
            try:
                csv_path.unlink(missing_ok=True)
            except OSError:
                pass

            # Drop the table once its measurements are recorded. At ten million
            # rows each table is over a gigabyte, and keeping all eleven would
            # need more space than is available. Everything is regenerable from
            # the seed, so nothing is lost.
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {spec.table} CASCADE")
            mark_dataset_complete(spec.name, n_rows)
            print(f"  dropped {spec.table}, marked complete")

    print(f"\nDone. Raw results: {RAW_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

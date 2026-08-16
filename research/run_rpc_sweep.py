"""
random_page_cost sweep.

The diagnostics point at one constant as the common root of the effects found so
far. `random_page_cost` defaults to 4.0, meaning a randomly fetched page is
assumed to cost four times a sequentially fetched one. That is a spinning-disk
assumption. On an SSD with a warm buffer pool the true ratio is close to 1.

This sweeps the setting across the full query set and records the same
measurements as the main experiment, so regret can be recomputed at each level
and the cost of the default quantified directly.

Output goes to results/raw/measurements_rpc.jsonl with the same schema as the
main sweep, plus a random_page_cost field.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import config  # noqa: E402
import datagen  # noqa: E402
import db  # noqa: E402
import queries as qmod  # noqa: E402
from config import (  # noqa: E402
    ALL_INDEXES_ARM,
    ALL_NO_BRIN_ARM,
    INDEX_ARMS,
    RAW_DIR,
    REPEATS,
    TARGET_SELECTIVITIES,
    WARMUP_RUNS,
    DatasetSpec,
)

RAW_PATH = RAW_DIR / "measurements_rpc.jsonl"
DATA_DIR = config.ROOT / "data"

RPC_LEVELS = (4.0, 3.0, 2.0, 1.5, 1.2, 1.1, 1.0)

# One dataset per factor, rather than the full grid. The question here is how
# the setting behaves, not how it interacts with every data property, and the
# sweep already multiplies the work by seven.
DATASETS = ("baseline", "skew10", "dep10", "phys05", "phys10")


def load_done() -> set[tuple]:
    done = set()
    if not RAW_PATH.exists():
        return done
    with RAW_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                done.add((r["dataset"], r["arm"], r["qid"], r["random_page_cost"]))
    return done


def main() -> int:
    from config import build_dataset_grid

    specs = [s for s in build_dataset_grid(1_000_000) if s.name in DATASETS]
    done = load_done()
    print(f"datasets={[s.name for s in specs]} rpc_levels={RPC_LEVELS}")
    print(f"already done: {len(done)}")

    arms = [a for a in INDEX_ARMS] + [ALL_NO_BRIN_ARM, ALL_INDEXES_ARM]

    with db.session() as conn:
        for spec in specs:
            print(f"\n=== {spec.name} ===", flush=True)
            ds = datagen.generate(spec, DATA_DIR)
            db.create_table(conn, spec.table)
            db.load_csv(conn, spec.table, ds.csv_path)
            db.analyze(conn, spec.table)
            query_set = qmod.build_queries(ds, TARGET_SELECTIVITIES)

            for arm in arms:
                pending_any = any(
                    (spec.name, arm.name, q.qid, rpc) not in done
                    for q in query_set
                    for rpc in RPC_LEVELS
                    if q.family in arm.supports
                )
                if not pending_any:
                    continue

                db.apply_index_arm(conn, spec.table, arm.ddl, prefix=f"ix_{spec.name}")

                for rpc in RPC_LEVELS:
                    with conn.cursor() as cur:
                        cur.execute("SET random_page_cost = %s", (rpc,))

                    n = 0
                    t0 = time.perf_counter()
                    for q in query_set:
                        if q.family not in arm.supports:
                            continue
                        if (spec.name, arm.name, q.qid, rpc) in done:
                            continue
                        m = db.measure(conn, q.sql, repeats=REPEATS, warmup=WARMUP_RUNS)
                        rec = {
                            "dataset": spec.name,
                            "dataset_spec": spec.to_dict(),
                            "arm": arm.name,
                            "random_page_cost": rpc,
                            "ext_stats": False,
                            **q.to_dict(),
                            **m,
                        }
                        with RAW_PATH.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps(rec, default=str) + "\n")
                        done.add((spec.name, arm.name, q.qid, rpc))
                        n += 1
                    if n:
                        print(
                            f"  {arm.name:20s} rpc={rpc:<4} {n:3d} queries "
                            f"in {time.perf_counter()-t0:.1f}s",
                            flush=True,
                        )

                with conn.cursor() as cur:
                    cur.execute("SET random_page_cost = 4.0")

            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {spec.table} CASCADE")
            try:
                ds.csv_path.unlink(missing_ok=True)
            except OSError:
                pass

    print(f"\nDone -> {RAW_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

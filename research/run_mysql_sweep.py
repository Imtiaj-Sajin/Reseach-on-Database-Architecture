"""
MariaDB optimiser-configuration sweep.

Counterpart to run_rpc_sweep.py, which swept PostgreSQL's random_page_cost.
MariaDB has no single equivalent constant, so this sweeps the settings that
govern the same three decisions:

  Multi-Range Read (mrr)
      MariaDB's closest analogue to PostgreSQL's bitmap scan. It buffers and
      sorts row identifiers before fetching them, converting random heap
      access into something closer to sequential. It ships DISABLED.

      This is the central experiment. We attributed MariaDB's 12.8x higher
      execution cost to the absence of a bitmap intermediate. If enabling MRR
      closes that gap, the deficit is a default-configuration choice. If it
      does not, the deficit is architectural. Both answers are informative and
      we do not know which to expect.

  optimizer_use_condition_selectivity
      How much statistical machinery the optimiser uses when estimating
      selectivity: 1 ignores most of it, 5 uses histograms most aggressively.
      The nearest thing to PostgreSQL's extended-statistics question.

  index_merge
      Directly tests the cross-system finding. If disabling index merge
      removes the correlated-predicate failure, that confirms the merge plan
      shape is the cause rather than an incidental correlate.

Output: results/raw/mysql_sweep.jsonl, same record schema as the other runs
plus a `config` field naming the setting combination.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import config as cfg  # noqa: E402
from config import RAW_DIR, REPEATS, TARGET_SELECTIVITIES, WARMUP_RUNS  # noqa: E402
from config import build_dataset_grid  # noqa: E402
import datagen  # noqa: E402
import db_mysql as db  # noqa: E402
import queries as qmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from run_experiment_mysql import MYSQL_ARMS, MYSQL_ALL_ARM  # noqa: E402

RAW_PATH = RAW_DIR / "mysql_sweep.jsonl"
DATA_DIR = cfg.ROOT / "data"

# One dataset per factor, matching the PostgreSQL cost sweep's selection.
DATASETS = ("baseline", "skew10", "dep10", "phys05", "phys10")

# Each entry is (name, list of SET statements applied per session).
CONFIGS: list[tuple[str, tuple[str, ...]]] = [
    ("default", ()),
    ("mrr_on", ("SET SESSION optimizer_switch='mrr=on'",)),
    ("mrr_on_sorted", ("SET SESSION optimizer_switch='mrr=on,mrr_sort_keys=on'",)),
    ("mrr_on_costbased", ("SET SESSION optimizer_switch='mrr=on,mrr_cost_based=on'",)),
    ("ucs1", ("SET SESSION optimizer_use_condition_selectivity=1",)),
    ("ucs2", ("SET SESSION optimizer_use_condition_selectivity=2",)),
    ("ucs3", ("SET SESSION optimizer_use_condition_selectivity=3",)),
    ("ucs5", ("SET SESSION optimizer_use_condition_selectivity=5",)),
    ("no_index_merge", ("SET SESSION optimizer_switch='index_merge=off'",)),
]

RESET = (
    "SET SESSION optimizer_switch='mrr=off,mrr_sort_keys=off,mrr_cost_based=off,index_merge=on'",
    "SET SESSION optimizer_use_condition_selectivity=4",
)


def load_done() -> set[tuple]:
    done = set()
    if not RAW_PATH.exists():
        return done
    with RAW_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                done.add((r["dataset"], r["arm"], r["qid"], r["config"]))
    return done


def apply(conn, stmts) -> None:
    with conn.cursor() as cur:
        for s in stmts:
            cur.execute(s)


def main() -> int:
    n_rows = 1_000_000
    specs = [s for s in build_dataset_grid(n_rows) if s.name in DATASETS]
    done = load_done()
    print(f"datasets={[s.name for s in specs]}")
    print(f"configs={[c[0] for c in CONFIGS]}")
    print(f"already done: {len(done)}")

    arms = list(MYSQL_ARMS) + [MYSQL_ALL_ARM]

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
                    (spec.name, arm.name, q.qid, cname) not in done
                    for q in query_set for cname, _ in CONFIGS
                    if q.family in arm.supports
                )
                if not pending_any:
                    continue
                db.apply_index_arm(conn, spec.table, arm.ddl, prefix=f"ix_{spec.name}")

                for cname, stmts in CONFIGS:
                    apply(conn, RESET)
                    apply(conn, stmts)
                    n = 0
                    t0 = time.perf_counter()
                    for q in query_set:
                        if q.family not in arm.supports:
                            continue
                        if (spec.name, arm.name, q.qid, cname) in done:
                            continue
                        ts = time.time()
                        m = db.measure(conn, q.sql, repeats=REPEATS, warmup=WARMUP_RUNS)
                        rec = {
                            "measured_at": ts,
                            "measured_wall_seconds": time.time() - ts,
                            "dbms": "mariadb",
                            "config": cname,
                            "dataset": spec.name,
                            "dataset_spec": spec.to_dict(),
                            "arm": arm.name,
                            "ext_stats": False,
                            **q.to_dict(),
                            **m,
                        }
                        with RAW_PATH.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps(rec, default=str) + "\n")
                        done.add((spec.name, arm.name, q.qid, cname))
                        n += 1
                    if n:
                        print(f"  {arm.name:20s} {cname:18s} {n:3d} queries "
                              f"in {time.perf_counter()-t0:6.1f}s", flush=True)
                apply(conn, RESET)

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

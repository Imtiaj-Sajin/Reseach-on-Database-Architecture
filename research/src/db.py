"""
PostgreSQL access layer: schema management, bulk loading, index arms, and the
measurement primitive.

Measurement instrument
----------------------
Every timed run goes through EXPLAIN (ANALYZE, BUFFERS, TIMING OFF,
FORMAT JSON) and we read the top-level "Execution Time" field.

Three reasons for that choice, all of which belong in the methodology section:

1. EXPLAIN ANALYZE executes the query server side and discards the result set,
   so client transfer time never enters the measurement. A plain timed
   SELECT would measure the network and the Python driver as much as the plan.
2. TIMING OFF disables per-node instrumentation. With TIMING ON, PostgreSQL
   calls the clock twice per emitted row, which on a 500k-row sequential scan
   can add a large and, importantly, *plan-dependent* overhead. That would
   systematically bias the comparison towards plans that emit fewer rows,
   which is exactly the comparison we are trying to make.
3. Using one instrument for every arm means any residual overhead is common
   mode and cancels in the ratio that defines regret.
"""
from __future__ import annotations

import json
import statistics
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg2
import psycopg2.extensions

from config import DB, MAINTENANCE_DB, SESSION_SETTINGS

SCHEMA_DDL = """
CREATE TABLE {t} (
    id        bigint      NOT NULL,
    k_uniform integer     NOT NULL,
    k_skew    integer     NOT NULL,
    a         integer     NOT NULL,
    b         integer     NOT NULL,
    ts        bigint      NOT NULL,
    payload   text        NOT NULL
)
"""


def connect(dbname: str | None = None) -> psycopg2.extensions.connection:
    params = dict(DB)
    if dbname:
        params["dbname"] = dbname
    conn = psycopg2.connect(**params)
    conn.autocommit = True
    return conn


def ensure_database() -> None:
    """Create the experiment database if it does not already exist."""
    conn = connect(MAINTENANCE_DB)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB["dbname"],))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{DB["dbname"]}"')
    finally:
        conn.close()


@contextmanager
def session() -> Iterator[psycopg2.extensions.connection]:
    """A connection with the experiment's session settings applied."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            for k, v in SESSION_SETTINGS.items():
                cur.execute(f"SET {k} = %s", (v,))
        yield conn
    finally:
        conn.close()


def server_metadata(conn) -> dict:
    """Capture the environment for the reproducibility section."""
    out: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT version()")
        out["version"] = cur.fetchone()[0]
        for setting in (
            "shared_buffers",
            "work_mem",
            "effective_cache_size",
            "random_page_cost",
            "seq_page_cost",
            "cpu_tuple_cost",
            "cpu_index_tuple_cost",
            "cpu_operator_cost",
            "max_parallel_workers_per_gather",
            "jit",
            "block_size",
            "default_statistics_target",
        ):
            cur.execute("SELECT current_setting(%s)", (setting,))
            out[setting] = cur.fetchone()[0]
    return out


def create_table(conn, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        cur.execute(SCHEMA_DDL.format(t=table))


def load_csv(conn, table: str, csv_path: Path) -> None:
    """Bulk load with COPY. Physical row order follows CSV order, which is what
    makes the physical-correlation factor meaningful."""
    with conn.cursor() as cur, csv_path.open("r", encoding="utf-8") as fh:
        cur.copy_expert(
            f"COPY {table} (id, k_uniform, k_skew, a, b, ts, payload) FROM STDIN WITH (FORMAT csv)",
            fh,
        )


def analyze(conn, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = 0")
        try:
            cur.execute(f"ANALYZE {table}")
        finally:
            cur.execute(
                "SET statement_timeout = %s", (SESSION_SETTINGS["statement_timeout"],)
            )


def table_stats(conn, table: str) -> dict:
    """Read back what PostgreSQL believes about the table.

    The pg_stats correlation column is the planner's own estimate of physical
    ordering, so comparing it against the correlation we designed into the data
    is a direct check that the generator did what it claims.
    """
    out: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_relation_size(%s), pg_total_relation_size(%s), "
            "(SELECT reltuples FROM pg_class WHERE relname = %s)",
            (table, table, table),
        )
        rel, total, reltuples = cur.fetchone()
        out["heap_bytes"] = int(rel)
        out["total_bytes"] = int(total)
        out["reltuples"] = float(reltuples)

        cur.execute(
            "SELECT attname, n_distinct, correlation FROM pg_stats WHERE tablename = %s",
            (table,),
        )
        out["columns"] = {
            r[0]: {"n_distinct": r[1], "correlation": r[2]} for r in cur.fetchall()
        }
    return out


def drop_all_indexes(conn, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = %s",
            (table,),
        )
        for (name,) in cur.fetchall():
            cur.execute(f'DROP INDEX IF EXISTS "{name}"')


def apply_index_arm(conn, table: str, arm_ddl: tuple[str, ...], prefix: str) -> dict:
    """Drop every index and build exactly the ones this arm defines.

    statement_timeout is lifted for the duration of the DDL. It exists to stop
    a runaway *query*, and at ten million rows a legitimate index build can
    exceed it: the hash index build on that table takes longer than the ten
    minute guard, which aborted an earlier run. Index build time is not a
    measured quantity, so removing the limit here affects no result.
    """
    drop_all_indexes(conn, table)
    built = []
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = 0")
        try:
            for stmt in arm_ddl:
                sql = stmt.format(ix=prefix, t=table)
                cur.execute(sql)
                built.append(sql)
        finally:
            cur.execute(
                "SET statement_timeout = %s", (SESSION_SETTINGS["statement_timeout"],)
            )
    analyze(conn, table)

    sizes = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT indexname, pg_relation_size(indexname::regclass) "
            "FROM pg_indexes WHERE tablename = %s",
            (table,),
        )
        sizes = {r[0]: int(r[1]) for r in cur.fetchall()}
    return {"ddl": built, "index_bytes": sizes}


def drop_extended_stats(conn, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT stxname FROM pg_statistic_ext e "
            "JOIN pg_class c ON c.oid = e.stxrelid WHERE c.relname = %s",
            (table,),
        )
        for (name,) in cur.fetchall():
            cur.execute(f'DROP STATISTICS IF EXISTS "{name}"')


def create_extended_stats(conn, table: str, ddl: str, name: str) -> None:
    """Create the extended statistics object, replacing any existing one.

    Dropping first is necessary because several index arms are re-measured in
    the extended-statistics state, and each of them calls this.
    """
    with conn.cursor() as cur:
        cur.execute(f'DROP STATISTICS IF EXISTS "{name}"')
        cur.execute(ddl.format(st=name, t=table))
    analyze(conn, table)


# --------------------------------------------------------------------------
# Plan inspection
# --------------------------------------------------------------------------
_SCAN_NODES = {
    "Seq Scan",
    "Index Scan",
    "Index Only Scan",
    "Bitmap Heap Scan",
    "Bitmap Index Scan",
    "Tid Scan",
}


def _walk(node: dict) -> Iterator[dict]:
    yield node
    for child in node.get("Plans", []) or []:
        yield from _walk(child)


def summarise_plan(plan_root: dict) -> dict:
    """Extract the access path actually used, plus estimate/actual row counts.

    A bitmap plan has the row estimate on the Bitmap Heap Scan and the index
    name on the Bitmap Index Scan beneath it, so we collect across the whole
    subtree rather than looking at a single node.
    """
    scans = [n for n in _walk(plan_root) if n.get("Node Type") in _SCAN_NODES]
    indexes = [n["Index Name"] for n in scans if "Index Name" in n]
    node_types = [n["Node Type"] for n in scans]

    # The row estimate that matters is the one on the topmost scan node, since
    # that is where the predicate has been fully applied.
    top = scans[0] if scans else plan_root
    est = float(top.get("Plan Rows", plan_root.get("Plan Rows", 0)))
    act = float(top.get("Actual Rows", plan_root.get("Actual Rows", 0)))

    if "Seq Scan" in node_types:
        access = "seqscan"
    elif "Index Only Scan" in node_types:
        access = "indexonlyscan"
    elif "Index Scan" in node_types:
        access = "indexscan"
    elif "Bitmap Heap Scan" in node_types:
        access = "bitmapscan"
    else:
        access = node_types[0] if node_types else "unknown"

    return {
        "access_path": access,
        "indexes_used": indexes,
        "node_types": node_types,
        "est_rows": est,
        "actual_rows": act,
        "total_cost": float(plan_root.get("Total Cost", 0.0)),
        "shared_hit": int(plan_root.get("Shared Hit Blocks", 0) or 0),
        "shared_read": int(plan_root.get("Shared Read Blocks", 0) or 0),
    }


def measure(conn, sql: str, repeats: int, warmup: int) -> dict:
    """Execute one query repeatedly and return timing plus plan facts.

    The first `warmup` runs are discarded so that the reported figures describe
    steady-state warm-cache behaviour. Cold-cache behaviour is a separate
    question that this design deliberately does not attempt to answer, because
    reliably evicting the OS page cache is not portable.
    """
    explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON) {sql}"
    times: list[float] = []
    plan_summary: dict | None = None

    with conn.cursor() as cur:
        for i in range(warmup + repeats):
            cur.execute(explain_sql)
            payload = cur.fetchone()[0]
            if isinstance(payload, str):
                payload = json.loads(payload)
            entry = payload[0]
            if i < warmup:
                continue
            times.append(float(entry["Execution Time"]))
            if plan_summary is None:
                plan_summary = summarise_plan(entry["Plan"])
                plan_summary["planning_time_ms"] = float(entry.get("Planning Time", 0.0))

    assert plan_summary is not None
    return {
        "exec_ms_median": statistics.median(times),
        "exec_ms_min": min(times),
        "exec_ms_max": max(times),
        "exec_ms_mean": statistics.fmean(times),
        "exec_ms_stdev": statistics.stdev(times) if len(times) > 1 else 0.0,
        "runs": times,
        **plan_summary,
    }

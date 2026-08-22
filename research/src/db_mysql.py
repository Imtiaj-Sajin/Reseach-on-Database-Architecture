"""
MariaDB access layer, mirroring db.py so the same analysis code works on both
systems.

Why a second system
-------------------
The PostgreSQL study cannot distinguish findings that are properties of
cost-based access-path selection in general from findings that are artefacts
of one implementation. Measuring a second optimiser is the only way to tell.

What does and does not carry over
---------------------------------
MariaDB has no BRIN index, so the headline PostgreSQL result cannot be
replicated directly and we do not pretend otherwise. What it can test:

  * whether near-optimal access-path selection is general or PostgreSQL-specific
  * whether cardinality misestimation likewise fails to explain misselection
  * whether index_merge, the direct analogue of PostgreSQL's BitmapAnd,
    mishandles correlated predicates the same way

The third is the valuable one. If index_merge shows the same defect, the
finding becomes architectural to merge-style plans rather than a quirk of one
codebase.

Measurement instrument
----------------------
ANALYZE FORMAT=JSON is MariaDB's equivalent of EXPLAIN ANALYZE: it executes the
query server side, discards the result set, and reports both estimated rows
(`rows`) and actual rows (`r_rows`) plus `r_total_time_ms`. As in the
PostgreSQL harness, using one instrument for every arm means any residual
overhead is common mode and cancels in the regret ratio.
"""
from __future__ import annotations

import json
import os
import statistics
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pymysql

MYSQL = {
    "host": os.environ.get("MYHOST", "127.0.0.1"),
    "port": int(os.environ.get("MYPORT", 3307)),
    "user": os.environ.get("MYUSER", "root"),
    "password": os.environ.get("MYPASSWORD", ""),
    "database": os.environ.get("MYDATABASE", "accesspath"),
}

# Session settings, chosen to correspond to the PostgreSQL configuration.
SESSION_SETTINGS = {
    # MariaDB has no per-session equivalent of disabling parallel gather;
    # InnoDB single-query execution is serial by default, which matches the
    # PostgreSQL configuration where max_parallel_workers_per_gather = 0.
    "max_statement_time": "600",
}

SCHEMA_DDL = """
CREATE TABLE {t} (
    id        BIGINT      NOT NULL,
    k_uniform INT         NOT NULL,
    k_skew    INT         NOT NULL,
    a         INT         NOT NULL,
    b         INT         NOT NULL,
    ts        BIGINT      NOT NULL,
    payload   VARCHAR(64) NOT NULL
) ENGINE=InnoDB
"""


def connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        local_infile=True, autocommit=True, charset="utf8mb4", **MYSQL
    )


@contextmanager
def session() -> Iterator[pymysql.connections.Connection]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            for k, v in SESSION_SETTINGS.items():
                try:
                    cur.execute(f"SET SESSION {k} = %s", (v,))
                except pymysql.err.MySQLError:
                    pass  # setting absent in this version; not essential
        yield conn
    finally:
        conn.close()


def server_metadata(conn) -> dict:
    out: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT VERSION()")
        out["version"] = cur.fetchone()[0]
        for var in (
            "innodb_buffer_pool_size",
            "innodb_page_size",
            "key_buffer_size",
            "join_buffer_size",
            "sort_buffer_size",
            "optimizer_switch",
            "use_stat_tables",
            "histogram_size",
            "histogram_type",
            "optimizer_use_condition_selectivity",
        ):
            cur.execute("SHOW VARIABLES LIKE %s", (var,))
            row = cur.fetchone()
            if row:
                out[var] = row[1]
    return out


def create_table(conn, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(SCHEMA_DDL.format(t=table))


def load_csv(conn, table: str, csv_path: Path) -> None:
    """Bulk load. Physical row order follows file order, as with COPY."""
    p = str(csv_path).replace("\\", "/")
    with conn.cursor() as cur:
        cur.execute(
            f"LOAD DATA LOCAL INFILE '{p}' INTO TABLE {table} "
            "FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' "
            "LINES TERMINATED BY '\\n' "
            "(id, k_uniform, k_skew, a, b, ts, payload)"
        )


def analyze(conn, table: str) -> None:
    """Collect engine-independent statistics and histograms.

    PERSISTENT FOR ALL is the closest analogue to PostgreSQL's ANALYZE: it
    gathers per-column statistics including histograms, which is what the
    optimiser consults for selectivity estimation.

    It writes those statistics into mysql.table_stats, mysql.column_stats and
    mysql.index_stats, which are Aria tables. After a power interruption
    damaged those tables the server died mid-statement while writing to them,
    taking the whole sweep with it. We therefore fall back to a plain ANALYZE
    if the persistent form fails, and report the downgrade rather than
    continuing silently: plain ANALYZE refreshes InnoDB's own estimates but
    not the histograms, which changes what the optimiser has to work with.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(f"ANALYZE TABLE {table} PERSISTENT FOR ALL")
            cur.fetchall()
        return
    except pymysql.err.MySQLError as exc:
        print(f"    [warn] PERSISTENT statistics failed on {table}: {exc}")
        print("    [warn] falling back to plain ANALYZE (no histograms)")

    if not conn.open:
        conn.ping(reconnect=True)
    with conn.cursor() as cur:
        cur.execute(f"ANALYZE TABLE {table}")
        cur.fetchall()


def table_stats(conn, table: str) -> dict:
    out: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT data_length, index_length, table_rows "
            "FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s",
            (MYSQL["database"], table),
        )
        row = cur.fetchone()
        if row:
            out["heap_bytes"] = int(row[0] or 0)
            out["index_bytes"] = int(row[1] or 0)
            out["reltuples"] = float(row[2] or 0)
    return out


def drop_all_indexes(conn, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT index_name FROM information_schema.statistics "
            "WHERE table_schema = %s AND table_name = %s AND index_name <> 'PRIMARY'",
            (MYSQL["database"], table),
        )
        for (name,) in cur.fetchall():
            cur.execute(f"DROP INDEX `{name}` ON {table}")


def apply_index_arm(conn, table: str, arm_ddl: tuple[str, ...], prefix: str) -> dict:
    drop_all_indexes(conn, table)
    built = []
    import time as _time

    t0 = _time.perf_counter()
    with conn.cursor() as cur:
        for stmt in arm_ddl:
            sql = stmt.format(ix=prefix, t=table)
            cur.execute(sql)
            built.append(sql)
    build_s = _time.perf_counter() - t0
    analyze(conn, table)

    sizes = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT index_name, SUM(stat_value) * @@innodb_page_size "
            "FROM mysql.innodb_index_stats "
            "WHERE database_name = %s AND table_name = %s AND stat_name = 'size' "
            "GROUP BY index_name",
            (MYSQL["database"], table),
        )
        for name, sz in cur.fetchall():
            sizes[name] = int(sz or 0)
    return {"ddl": built, "index_bytes": sizes, "build_seconds": build_s}


# --------------------------------------------------------------------------
# Plan inspection
# --------------------------------------------------------------------------
# MariaDB access_type values mapped onto the vocabulary used for PostgreSQL,
# so the two systems can be compared in one table. The mapping is by role in
# the plan, not by name:
#   ALL          -> seqscan       full table scan
#   index        -> seqscan       full index scan, still touches every row
#   index_merge  -> bitmapscan    the BitmapAnd/BitmapOr analogue
#   ref/range/.. -> indexscan     ordinary index access
_ACCESS_MAP = {
    "ALL": "seqscan",
    "index": "seqscan",
    "index_merge": "bitmapscan",
    "ref": "indexscan",
    "eq_ref": "indexscan",
    "const": "indexscan",
    "range": "indexscan",
    "ref_or_null": "indexscan",
    "unique_subquery": "indexscan",
    "fulltext": "indexscan",
}


def _walk(node: Any) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def summarise_plan(payload: dict) -> dict:
    """Extract access path, indexes used, and estimated/actual rows."""
    qb = payload.get("query_block", {})
    nodes = list(_walk(qb))

    access_types = [n["access_type"] for n in nodes if "access_type" in n]
    keys = [n["key"] for n in nodes if isinstance(n.get("key"), str)]
    # index_merge exposes its constituent indexes under a different key
    for n in nodes:
        if "index_merge" in n:
            for sub in _walk(n["index_merge"]):
                if isinstance(sub.get("key"), str):
                    keys.append(sub["key"])

    raw = access_types[0] if access_types else "unknown"
    access = _ACCESS_MAP.get(raw, raw)

    # Row-count semantics differ from PostgreSQL and must be converted, or the
    # q-error comparison between the two systems is meaningless.
    #
    # PostgreSQL's Plan Rows / Actual Rows on a scan node are counts AFTER the
    # predicate is applied. MariaDB's `rows` / `r_rows` are rows EXAMINED, with
    # the surviving fraction reported separately as `filtered` / `r_filtered`
    # (percentages). A full table scan therefore reports rows = table
    # cardinality regardless of how selective the predicate is.
    #
    # Rows returned is the product. We compute it so that both systems report
    # the same quantity: the estimated and actual number of rows the predicate
    # yields.
    def _first(keys: tuple[str, ...]) -> float | None:
        for node in nodes:
            for k in keys:
                if k in node:
                    try:
                        return float(node[k])
                    except (TypeError, ValueError):
                        continue
        return None

    rows_examined = _first(("rows",))
    rows_actual_examined = _first(("r_rows",))
    filtered = _first(("filtered",))
    r_filtered = _first(("r_filtered",))

    est = (rows_examined or 0.0) * ((filtered if filtered is not None else 100.0) / 100.0)
    act = (rows_actual_examined or 0.0) * (
        (r_filtered if r_filtered is not None else 100.0) / 100.0
    )

    return {
        "access_path": access,
        "raw_access_type": raw,
        "indexes_used": sorted(set(keys)),
        "node_types": access_types,
        "est_rows": est,
        "actual_rows": act,
        # Retained so the conversion above can be audited from the raw records.
        "rows_examined_est": rows_examined or 0.0,
        "rows_examined_actual": rows_actual_examined or 0.0,
        "filtered_pct": filtered,
        "r_filtered_pct": r_filtered,
        "total_cost": 0.0,  # MariaDB 10.4 does not expose plan cost in JSON
        "shared_hit": 0,
        "shared_read": 0,
    }


def measure(conn, sql: str, repeats: int, warmup: int) -> dict:
    """Execute repeatedly via ANALYZE FORMAT=JSON and return timing plus plan."""
    times: list[float] = []
    plan_summary: dict | None = None

    with conn.cursor() as cur:
        for i in range(warmup + repeats):
            cur.execute(f"ANALYZE FORMAT=JSON {sql}")
            payload = cur.fetchone()[0]
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode("utf-8")
            data = json.loads(payload)
            if i < warmup:
                continue
            qb = data.get("query_block", {})
            t = qb.get("r_total_time_ms")
            if t is None:
                for n in _walk(qb):
                    if "r_total_time_ms" in n:
                        t = n["r_total_time_ms"]
                        break
            times.append(float(t or 0.0))
            if plan_summary is None:
                plan_summary = summarise_plan(data)
                plan_summary["planning_time_ms"] = 0.0

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

"""
Supervisor for the MariaDB optimiser sweep.

MariaDB 10.4.28 has crashed three times during this sweep, at 1024, 2304 and
(earlier) 67 records, each time silently: the server writes no diagnostic and
the client sees only a dropped connection. The third crash happened on a
freshly initialised data directory, so it is not corruption left by the power
interruption. It appears to be instability under sustained ANALYZE and query
load rather than anything specific to our data, since it recurs at different
datasets and different points.

Rather than keep restarting by hand, this supervises the run: start the server
if it is down, launch the sweep, and on failure restart and resume. The sweep
is keyed on (dataset, arm, query, config) and skips completed work, so each
attempt resumes exactly where the last died and nothing is remeasured.

The instability itself is worth reporting in the paper. A system that cannot
complete a four-hour read-mostly workload without crashing is a finding about
that system, not merely an inconvenience for us.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MYSQLD = Path(r"C:\xampp\mysql\bin\mysqld.exe")
MYSQL = Path(r"C:\xampp\mysql\bin\mysql.exe")
DEFAULTS = r"G:\mariadb_dtms\my.ini"
SWEEP = ROOT / "run_mysql_sweep.py"
RAW = ROOT / "results" / "raw" / "mysql_sweep.jsonl"
LOG = ROOT / "results" / "raw" / "mysql_sweep.log"

MAX_ATTEMPTS = 40


def records() -> int:
    if not RAW.exists():
        return 0
    with RAW.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def server_up() -> bool:
    try:
        r = subprocess.run(
            [str(MYSQL), "-u", "root", "-h", "127.0.0.1", "-P", "3307",
             "-N", "-e", "SELECT 1"],
            capture_output=True, timeout=20,
        )
        return r.returncode == 0 and b"1" in r.stdout
    except Exception:
        return False


def start_server() -> bool:
    if server_up():
        return True
    subprocess.Popen(
        [str(MYSQLD), f"--defaults-file={DEFAULTS}"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for _ in range(30):
        time.sleep(2)
        if server_up():
            return True
    return False


def main() -> int:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        before = records()
        print(f"[supervisor] attempt {attempt}, {before} records so far", flush=True)

        if not start_server():
            print("[supervisor] server would not start; giving up", flush=True)
            return 1

        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"\n===== supervisor attempt {attempt} =====\n")
            proc = subprocess.run(
                [sys.executable, str(SWEEP)],
                stdout=fh, stderr=subprocess.STDOUT,
            )

        after = records()
        if proc.returncode == 0:
            print(f"[supervisor] sweep completed, {after} records", flush=True)
            return 0

        gained = after - before
        print(f"[supervisor] attempt {attempt} failed after {gained} new records",
              flush=True)

        # A failure that produced nothing means restarting will not help:
        # something is wrong beyond the crash we are working around.
        if gained == 0 and attempt > 2:
            print("[supervisor] no progress across attempts; stopping", flush=True)
            return 1

        time.sleep(10)

    print("[supervisor] attempt limit reached", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

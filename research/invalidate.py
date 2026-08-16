"""
Remove measurements invalidated by the range-predicate targeting fix.

The old _range_bounds_for_target collapsed onto a single value on skewed
columns, so range predicates on the Zipfian datasets did not sweep selectivity
as intended. Those measurements are not wrong (the recorded row counts were
always exact) but they answer a different question than the one the design
asks, and mixing them with post-fix measurements inside one dataset would make
the results incoherent.

This drops every record for the affected datasets so they can be re-measured
cleanly. Unaffected datasets are left alone: re-running them would cost time
and add nothing, since the code path that changed does not touch them.

A timestamped backup is written before anything is removed.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import RAW_DIR  # noqa: E402

# Datasets whose range predicates collapsed. Verified empirically: these are
# the only ones where the number of distinct range predicates was below the
# number of targets requested.
AFFECTED = {"skew05", "skew10", "skew15"}


def main() -> int:
    total_removed = 0
    for path in sorted(RAW_DIR.glob("measurements_*.jsonl")):
        rows = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rows.append(json.loads(line))
        if not rows:
            continue

        keep = [r for r in rows if r.get("dataset") not in AFFECTED]
        removed = len(rows) - len(keep)
        if removed == 0:
            print(f"{path.name:26s} unchanged ({len(rows)} records)")
            continue

        backup = path.with_suffix(path.suffix + ".prefix_bak")
        shutil.copy2(path, backup)
        with path.open("w", encoding="utf-8") as fh:
            for r in keep:
                fh.write(json.dumps(r, default=str) + "\n")

        total_removed += removed
        print(
            f"{path.name:26s} removed {removed:5d}, kept {len(keep):5d}  "
            f"(backup -> {backup.name})"
        )

    print(f"\nTotal removed: {total_removed}")
    print("Re-run with: python run_experiment.py --rows N --only skew05,skew10,skew15")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

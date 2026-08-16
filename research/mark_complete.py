"""
Derive dataset-completion markers from existing measurement files.

run_experiment.py writes a marker when a dataset finishes, so a resume can skip
it without first spending minutes regenerating and reloading the table. Runs
that predate that mechanism, or that were killed by a power cut before writing
the marker, leave no markers behind. This reconstructs them.

A dataset counts as complete when it holds the same number of records as the
most common per-dataset count in the file. That modal count is the number of
measurements a full pass produces, so anything short of it was interrupted.
Deriving the threshold rather than hard-coding it keeps this correct if the
query grid or the set of index arms changes.

Safe to run repeatedly, and safe to run while a sweep is in progress: an
in-flight dataset is simply below the modal count and is left unmarked.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import RAW_DIR  # noqa: E402


def main() -> int:
    for path in sorted(RAW_DIR.glob("measurements_*.jsonl")):
        tag = path.stem.replace("measurements_", "")
        if tag == "rpc":
            continue  # different driver, different completion semantics

        counts: collections.Counter = collections.Counter()
        n_rows_by_dataset: dict[str, int] = {}
        for line in path.open(encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            counts[r["dataset"]] += 1
            spec = r.get("dataset_spec") or {}
            if "n_rows" in spec:
                n_rows_by_dataset[r["dataset"]] = int(spec["n_rows"])

        if not counts:
            continue

        full = collections.Counter(counts.values()).most_common(1)[0][0]
        complete = {
            name: n_rows_by_dataset[name]
            for name, c in counts.items()
            if c >= full and name in n_rows_by_dataset
        }
        partial = {name: c for name, c in counts.items() if c < full}

        marker = path.with_name(path.stem + "_completed.json")
        marker.write_text(json.dumps(complete, indent=2), encoding="utf-8")

        print(f"{path.name}")
        print(f"  full pass = {full} records")
        print(f"  complete  = {len(complete)} datasets -> {marker.name}")
        if partial:
            print(f"  partial   = {partial}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

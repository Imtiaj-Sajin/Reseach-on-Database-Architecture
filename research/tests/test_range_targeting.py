"""
Regression test for the range-predicate selectivity bug.

The original _range_bounds_for_target anchored a fixed-width window at the
median row position. On a Zipfian column the median row falls inside a single
very frequent value, so the window collapsed onto that one value and the
predicate returned its whole frequency regardless of the requested target.
Every small target produced an identical query, and the intended selectivity
sweep silently did not happen.

This test asserts what the old implementation failed: that distinct targets
produce distinct predicates, and that achieved selectivity tracks the target.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import datagen  # noqa: E402
import queries as qmod  # noqa: E402
from config import DatasetSpec  # noqa: E402

N = 200_000
TARGETS = (0.00001, 0.0001, 0.001, 0.01, 0.05, 0.10, 0.20, 0.50)
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' :: ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)


def main() -> int:
    tmp = Path(__file__).resolve().parent.parent / "data"

    for zipf_s in (0.0, 1.0, 1.5):
        print(f"\nzipf_s = {zipf_s}")
        spec = DatasetSpec(
            name=f"rt{zipf_s}".replace(".", ""), n_rows=N,
            zipf_s=zipf_s, dep_strength=0.0, physical_corr=0.0,
        )
        ds = datagen.generate(spec, tmp)
        qs = [q for q in qmod.build_queries(ds, TARGETS) if q.family == "range"]

        print(f"    {'target':>10} {'achieved':>10} {'rows':>10}  predicate")
        for q in sorted(qs, key=lambda x: x.target_selectivity):
            print(
                f"    {q.target_selectivity:>10.5f} {q.achieved_selectivity:>10.5f} "
                f"{q.true_rows:>10}  k_skew BETWEEN {q.params['lo']} AND {q.params['hi']}"
            )

        # 1. Distinct targets must yield distinct predicates.
        preds = [(q.params["lo"], q.params["hi"]) for q in qs]
        check(
            "distinct targets give distinct predicates",
            len(set(preds)) == len(preds),
            f"{len(set(preds))} unique of {len(preds)}",
        )

        # 2. Achieved selectivity must increase with the target.
        ordered = sorted(qs, key=lambda x: x.target_selectivity)
        ach = [q.achieved_selectivity for q in ordered]
        check(
            "achieved selectivity is non-decreasing in target",
            all(ach[i] <= ach[i + 1] + 1e-9 for i in range(len(ach) - 1)),
            str([round(a, 5) for a in ach]),
        )

        # 3. Ground truth must still be exact.
        exact = all(
            int(np.count_nonzero((ds.k_skew >= q.params["lo"]) & (ds.k_skew <= q.params["hi"])))
            == q.true_rows
            for q in qs
        )
        check("recorded row counts are exact", exact)

        # 4. The small end must actually be small. This is the specific
        #    failure: the old code returned 13.6 percent of the table for a
        #    0.001 percent target on skewed data.
        smallest = ordered[0]
        check(
            "smallest target stays below 1 percent of the table",
            smallest.achieved_selectivity < 0.01,
            f"achieved {smallest.achieved_selectivity:.5f}",
        )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED: {FAILURES}")
        return 1
    print("All range-targeting checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Validation of the data generator and query builder.

These checks matter because the entire study rests on the claim that we know
the true cardinality of every predicate and that each factor was actually
induced in the data. If the generator silently failed, every downstream number
would be wrong in a way that no amount of careful measurement would reveal.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import datagen  # noqa: E402
import queries as qmod  # noqa: E402
from config import DatasetSpec  # noqa: E402

N = 40_000
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}{(' :: ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="apr_test_"))

    print("\n1. Skew is actually induced")
    uniform = DatasetSpec(name="u", n_rows=N, zipf_s=0.0, dep_strength=0.0, physical_corr=0.0)
    skewed = DatasetSpec(name="s", n_rows=N, zipf_s=1.5, dep_strength=0.0, physical_corr=0.0)
    du = datagen.generate(uniform, tmp)
    dsk = datagen.generate(skewed, tmp)

    def top_share(counts: np.ndarray, k: int = 10) -> float:
        return float(np.sort(counts)[::-1][:k].sum() / counts.sum())

    su, ss = top_share(du.skew_counts), top_share(dsk.skew_counts)
    check("uniform top-10 share is small", su < 0.02, f"{su:.4f}")
    check("zipf s=1.5 top-10 share is large", ss > 0.5, f"{ss:.4f}")
    check("skew increases concentration", ss > su * 10, f"{ss:.4f} vs {su:.4f}")

    print("\n2. Functional dependency strength is honoured")
    for strength in (0.0, 0.5, 1.0):
        spec = DatasetSpec(
            name=f"d{strength}", n_rows=N, zipf_s=0.0,
            dep_strength=strength, physical_corr=0.0,
        )
        d = datagen.generate(spec, tmp)
        expected_b = (d.a * datagen._DEP_MULT + datagen._DEP_ADD) % spec.domain_b
        realised = float(np.mean(d.b == expected_b))
        # With independence, chance agreement is 1/domain_b.
        chance = 1.0 / spec.domain_b
        predicted = strength + (1 - strength) * chance
        check(
            f"dep_strength={strength} realised={realised:.4f}",
            abs(realised - predicted) < 0.02,
            f"predicted {predicted:.4f}",
        )

    print("\n3. Physical correlation is monotone in the parameter")
    corrs = []
    for p in (0.0, 0.5, 0.95, 1.0):
        spec = DatasetSpec(
            name=f"p{p}", n_rows=N, zipf_s=0.0, dep_strength=0.0, physical_corr=p,
        )
        d = datagen.generate(spec, tmp)
        corrs.append((p, d.realised_ts_rank_corr))
        print(f"      physical_corr={p:<5} realised rank corr={d.realised_ts_rank_corr:.4f}")
    check(
        "realised correlation increases with the parameter",
        all(corrs[i][1] <= corrs[i + 1][1] + 1e-6 for i in range(len(corrs) - 1)),
        str([round(c, 3) for _, c in corrs]),
    )
    check("corr=1.0 gives near-perfect ordering", corrs[-1][1] > 0.99, f"{corrs[-1][1]:.4f}")

    print("\n4. Query ground-truth cardinality is exact")
    spec = DatasetSpec(name="q", n_rows=N, zipf_s=1.0, dep_strength=0.75, physical_corr=0.9)
    d = datagen.generate(spec, tmp)
    qs = qmod.build_queries(d, (0.001, 0.01, 0.1, 0.5))
    print(f"      built {len(qs)} queries")

    ok_eq = ok_range = ok_conj = ok_ts = True
    for q in qs:
        if q.family == "eq":
            truth = int(np.count_nonzero(d.k_skew == q.params["k_skew"]))
            ok_eq &= truth == q.true_rows
        elif q.family == "range":
            truth = int(
                np.count_nonzero((d.k_skew >= q.params["lo"]) & (d.k_skew <= q.params["hi"]))
            )
            ok_range &= truth == q.true_rows
        elif q.family == "conj":
            truth = int(
                np.count_nonzero((d.a == q.params["a"]) & (d.b == q.params["b"]))
            )
            ok_conj &= truth == q.true_rows
        elif q.family == "ts_range":
            ts = d.ts_sorted
            truth = int(
                np.count_nonzero((ts >= q.params["lo"]) & (ts <= q.params["hi"]))
            )
            ok_ts &= truth == q.true_rows

    check("equality predicate counts are exact", ok_eq)
    check("range predicate counts are exact", ok_range)
    check("conjunctive predicate counts are exact", ok_conj)
    check("timestamp range counts are exact", ok_ts)

    print("\n5. Dependent conjunctions really are denser than independent ones")
    dep_rows = [q.true_rows for q in qs if q.params.get("variant") == "dependent"]
    indep_rows = [q.true_rows for q in qs if q.params.get("variant") == "independent"]
    check(
        "dependent conjunctions return more rows",
        sum(dep_rows) > sum(indep_rows),
        f"dep={sum(dep_rows)} indep={sum(indep_rows)}",
    )

    print("\n6. Reproducibility from the seed")
    a1 = datagen.generate(DatasetSpec(name="r1", n_rows=5000, zipf_s=1.0, dep_strength=0.5, physical_corr=0.5, seed=7), tmp)
    a2 = datagen.generate(DatasetSpec(name="r2", n_rows=5000, zipf_s=1.0, dep_strength=0.5, physical_corr=0.5, seed=7), tmp)
    check("same seed gives identical data", bool(np.array_equal(a1.k_skew, a2.k_skew) and np.array_equal(a1.b, a2.b)))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED: {FAILURES}")
        return 1
    print("All generator checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

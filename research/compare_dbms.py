"""Cross-DBMS comparison, corrected."""
import json
import sys
from pathlib import Path

import pandas as pd

RESEARCH = Path(r"g:\codes\Ass\DTMS\research")
sys.path.insert(0, str(RESEARCH / "src"))
import metrics  # noqa: E402

pd.set_option("display.width", 220)


def load(path, dbms):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                r["dbms"] = dbms
                rows.append(r)
    df = pd.DataFrame(rows)
    spec = pd.json_normalize(df["dataset_spec"])
    for c in ("zipf_s", "dep_strength", "physical_corr", "n_rows"):
        df[c] = spec[c].values
    df["indexes_used"] = df["indexes_used"].apply(
        lambda v: ",".join(v) if isinstance(v, list) else str(v))
    df["selectivity"] = df["true_rows"] / df["n_rows"]
    df["variant"] = df["params"].apply(
        lambda p: p.get("variant") if isinstance(p, dict) else None)
    df["scale"] = "1m"
    df["q_error"] = [metrics.q_error(e, a) for e, a in zip(df.est_rows, df.actual_rows)]
    return df


raw = RESEARCH / "results" / "raw"
pg = load(raw / "measurements_1m.jsonl", "postgresql")
my = load(raw / "mysql_measurements_1m.jsonl", "mariadb")

COMMON = {"none", "btree_skew", "btree_a_b_separate", "btree_ab_composite",
          "btree_ts", "all_no_brin"}
pgc = pg[pg.arm.isin(COMMON) & (~pg.ext_stats)].copy()
myc = my[my.arm.isin(COMMON)].copy()

sets = {"postgresql": pgc, "mariadb": myc}
regs = {k: metrics.compute_regret(v) for k, v in sets.items()}

print("=" * 76)
print("WHERE DOES MARIADB LOSE? regret by family")
print("=" * 76)
for name, reg in regs.items():
    r = reg[reg.reliable]
    if not len(r):
        continue
    t = r.groupby("family").agg(
        n=("regret", "size"),
        mis=("misselected", lambda x: round(x.mean() * 100, 1)),
        med=("regret", "median"), mx=("regret", "max"),
        qerr=("q_error", "median"))
    print("\n%s:" % name)
    print(t.round(3).to_string())

print()
print("=" * 76)
print("WHAT DOES EACH SYSTEM CHOOSE? free-choice arm")
print("=" * 76)
for name, df in sets.items():
    a = df[df.arm == "all_no_brin"]
    print("\n%s:" % name)
    print(pd.crosstab(a.family, a.access_path).to_string())

print()
print("=" * 76)
print("Q3: CORRELATED CONJUNCTIONS -- BitmapAnd vs index_merge")
print("=" * 76)
for name, df in sets.items():
    c = df[(df.family == "conj") & (df.arm == "all_no_brin")].copy()
    if not len(c):
        continue
    print("\n%s  median q-error by dependency strength and variant:" % name)
    print(c.pivot_table(index="dep_strength", columns="variant",
                        values="q_error", aggfunc="median").round(2).to_string())
    print("%s  access paths chosen on conj:" % name)
    print(c.access_path.value_counts().to_string())
    if name == "mariadb":
        print("%s  raw access types:" % name)
        print(c.raw_access_type.value_counts().to_string())

print()
print("=" * 76)
print("MARIADB WORST CASES")
print("=" * 76)
r = regs["mariadb"]
r = r[r.reliable].nlargest(10, "regret")
cols = ["dataset", "family", "qid", "selectivity", "true_rows", "est_rows",
        "q_error", "access_path", "best_arm", "exec_ms_median", "best_ms", "regret"]
print(r[[c for c in cols if c in r.columns]].round(3).to_string(index=False))

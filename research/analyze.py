"""
Turn raw measurements into the figures and tables the paper will use.

Run after run_experiment.py:

    python analyze.py

Everything written here is derived from results/raw/measurements.jsonl. No
number in any output is typed by hand.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import FIGURES_DIR, RAW_DIR, TABLES_DIR  # noqa: E402
import metrics  # noqa: E402

# The random_page_cost sweep writes the same schema but varies a planner
# setting rather than the data scale. Folding it into the main table would
# blend seven cost-model configurations into one misselection rate, so it is
# loaded and reported separately.
RPC_TAG = "rpc"


def raw_files() -> list[Path]:
    """Scale-tagged measurement files, e.g. measurements_1m.jsonl."""
    return sorted(
        p for p in RAW_DIR.glob("measurements_*.jsonl")
        if p.stem.replace("measurements_", "") != RPC_TAG
    )


def rpc_file() -> Path | None:
    p = RAW_DIR / f"measurements_{RPC_TAG}.jsonl"
    return p if p.exists() else None

# Consistent, colour-blind-safe palette used across every figure.
PALETTE = {
    "seqscan": "#4C72B0",
    "indexscan": "#DD8452",
    "bitmapscan": "#55A868",
    "indexonlyscan": "#C44E52",
    "unknown": "#8C8C8C",
}

plt.rcParams.update(
    {
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "font.size": 9,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def load_raw(paths: list[Path] | None = None) -> pd.DataFrame:
    rows = []
    for path in (paths if paths is not None else raw_files()):
        tag = path.stem.replace("measurements_", "")
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    rec["scale"] = tag
                    rows.append(rec)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)

    # Flatten the dataset spec so factors are first-class columns.
    spec = pd.json_normalize(df["dataset_spec"])
    for col in ("zipf_s", "dep_strength", "physical_corr", "n_rows"):
        df[col] = spec[col].values

    df["indexes_used"] = df["indexes_used"].apply(
        lambda v: ",".join(v) if isinstance(v, list) else ""
    )
    df["selectivity"] = df["true_rows"] / df["n_rows"]
    return df


def fig_estimation_error(reg: pd.DataFrame) -> None:
    """How badly the planner misjudges cardinality, by factor level."""
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))

    # Panel A: dependency strength versus q-error on conjunctive predicates.
    conj = reg[(reg["family"] == "conj") & (~reg["ext_stats"])]
    if not conj.empty:
        ax = axes[0]
        for variant, marker in (("dependent", "o"), ("independent", "s")):
            sub = conj[conj["variant"] == variant]
            if sub.empty:
                continue
            g = sub.groupby("dep_strength")["q_error"].median()
            ax.plot(g.index, g.values, marker=marker, label=variant, linewidth=1.6)
        ax.set_yscale("log")
        ax.axhline(1.0, color="black", linewidth=0.8, linestyle=":")
        ax.set_xlabel("functional dependency strength")
        ax.set_ylabel("median q-error")
        ax.set_title("A. Conjunctive predicates", loc="left", fontsize=10)
        ax.legend(frameon=False, fontsize=8)

    # Panel B: q-error against selectivity for single-column predicates.
    single = reg[reg["family"].isin(["eq", "range", "ts_range"])]
    if not single.empty:
        ax = axes[1]
        for fam in ("eq", "range", "ts_range"):
            sub = single[single["family"] == fam]
            if sub.empty:
                continue
            ax.scatter(
                sub["selectivity"], sub["q_error"], s=12, alpha=0.6, label=fam
            )
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.axhline(1.0, color="black", linewidth=0.8, linestyle=":")
        ax.set_xlabel("true selectivity")
        ax.set_ylabel("q-error")
        ax.set_title("B. Single-column predicates", loc="left", fontsize=10)
        ax.legend(frameon=False, fontsize=8)

    fig.suptitle("Cardinality estimation error", fontsize=11, y=1.02)
    fig.savefig(FIGURES_DIR / "fig1_estimation_error.png")
    plt.close(fig)


def fig_regret_by_selectivity(reg: pd.DataFrame) -> None:
    """The headline figure: where in the selectivity range the planner loses."""
    d = reg[reg["reliable"]]
    if d.empty:
        return
    fig, ax = plt.subplots(figsize=(6.2, 3.6))

    for fam, colour in zip(
        ("eq", "range", "ts_range", "conj"), ("#4C72B0", "#DD8452", "#55A868", "#C44E52")
    ):
        sub = d[d["family"] == fam]
        if sub.empty:
            continue
        ax.scatter(
            sub["selectivity"], sub["regret"], s=18, alpha=0.7, label=fam, color=colour
        )

    ax.axhline(1.0, color="black", linewidth=0.9, linestyle="--", label="optimal")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("true selectivity (fraction of table returned)")
    ax.set_ylabel("regret  (chosen time / best time)")
    ax.set_title("Access-path regret against selectivity", loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.savefig(FIGURES_DIR / "fig2_regret_by_selectivity.png")
    plt.close(fig)


def fig_qerror_vs_regret(reg: pd.DataFrame) -> None:
    """Does misestimation actually cause bad plans? The core causal claim."""
    d = reg[reg["reliable"]]
    if d.empty:
        return
    fig, ax = plt.subplots(figsize=(5.4, 3.8))

    for direction, colour, marker in (
        ("under", "#C44E52", "v"),
        ("over", "#4C72B0", "^"),
        ("accurate", "#8C8C8C", "o"),
    ):
        sub = d[d["direction"] == direction]
        if sub.empty:
            continue
        ax.scatter(
            sub["q_error"], sub["regret"], s=20, alpha=0.7,
            label=direction, color=colour, marker=marker,
        )

    ax.axhline(1.0, color="black", linewidth=0.9, linestyle="--")
    ax.axvline(1.0, color="black", linewidth=0.8, linestyle=":")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("q-error (cardinality misestimation)")
    ax.set_ylabel("regret")
    ax.set_title("Does misestimation cause plan regret?", loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=8, title="estimate direction", title_fontsize=8)
    fig.savefig(FIGURES_DIR / "fig3_qerror_vs_regret.png")
    plt.close(fig)


def fig_extended_stats(reg: pd.DataFrame) -> None:
    """The extended-statistics question, answered on two axes at once.

    Extended statistics are sold as fixing correlated-predicate estimation. The
    interesting question is whether fixing the *estimate* also fixes the
    *plan*, because those can come apart.
    """
    conj = reg[reg["family"] == "conj"]
    if conj.empty or conj["ext_stats"].nunique() < 2:
        return

    dep = conj[conj["variant"] == "dependent"]
    if dep.empty:
        return

    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))

    ax = axes[0]
    for flag, label, colour in ((False, "without", "#C44E52"), (True, "with", "#55A868")):
        sub = dep[dep["ext_stats"] == flag]
        if sub.empty:
            continue
        g = sub.groupby("dep_strength")["q_error"].median()
        ax.plot(g.index, g.values, marker="o", label=label, color=colour, linewidth=1.7)
    ax.set_yscale("log")
    ax.axhline(1.0, color="black", linewidth=0.8, linestyle=":")
    ax.set_xlabel("functional dependency strength")
    ax.set_ylabel("median q-error")
    ax.set_title("A. Effect on the estimate", loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=8, title="extended statistics", title_fontsize=8)

    ax = axes[1]
    d = dep[dep["reliable"]]
    if not d.empty:
        for flag, label, colour in ((False, "without", "#C44E52"), (True, "with", "#55A868")):
            sub = d[d["ext_stats"] == flag]
            if sub.empty:
                continue
            g = sub.groupby("dep_strength")["regret"].median()
            ax.plot(g.index, g.values, marker="o", label=label, color=colour, linewidth=1.7)
        ax.axhline(1.0, color="black", linewidth=0.8, linestyle="--")
        ax.set_xlabel("functional dependency strength")
        ax.set_ylabel("median regret")
    ax.set_title("B. Effect on the plan", loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=8)

    fig.suptitle("Do extended statistics fix index misselection?", fontsize=11, y=1.03)
    fig.savefig(FIGURES_DIR / "fig4_extended_statistics.png")
    plt.close(fig)


def fig_brin_correlation(raw: pd.DataFrame) -> None:
    """BRIN lives or dies on physical correlation. Does the planner track it?"""
    d = raw[(raw["family"] == "ts_range") & (raw["arm"].isin(["brin_ts", "btree_ts", "none", "all"]))]
    if d.empty:
        return
    fig, ax = plt.subplots(figsize=(6.2, 3.6))

    for arm, colour in (
        ("none", "#4C72B0"),
        ("brin_ts", "#55A868"),
        ("btree_ts", "#DD8452"),
        ("all", "#C44E52"),
    ):
        sub = d[d["arm"] == arm]
        if sub.empty:
            continue
        g = sub.groupby("physical_corr")["exec_ms_median"].median()
        ax.plot(g.index, g.values, marker="o", label=arm, color=colour, linewidth=1.7)

    ax.set_yscale("log")
    ax.set_xlabel("physical correlation of the indexed column")
    ax.set_ylabel("median execution time (ms)")
    ax.set_title("BRIN viability against physical correlation", loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=8, title="index arm", title_fontsize=8)
    fig.savefig(FIGURES_DIR / "fig5_brin_correlation.png")
    plt.close(fig)


def fig_random_page_cost(rpc_raw: pd.DataFrame, rpc_reg: pd.DataFrame) -> None:
    """What the spinning-disk default costs on an SSD.

    Panel A is the practitioner-facing number: total time across the whole
    query set at each setting. Panel B separates the two planner arms, so the
    BRIN effect is not mistaken for a cost-model effect.
    """
    if rpc_raw.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 3.5))

    ax = axes[0]
    planner = rpc_raw[rpc_raw["arm"] == "all_no_brin"]
    if not planner.empty:
        g = planner.groupby("random_page_cost")["exec_ms_median"].sum()
        ax.plot(g.index, g.values, marker="o", color="#4C72B0", linewidth=1.8)
        default = g.get(4.0)
        best = g.min()
        if default and best:
            ax.annotate(
                f"default 4.0 costs {default/best:.2f}x\nthe best setting",
                xy=(4.0, default), xytext=(2.2, default * 0.92),
                fontsize=8, arrowprops=dict(arrowstyle="->", lw=0.8),
            )
    ax.invert_xaxis()
    ax.set_xlabel("random_page_cost")
    ax.set_ylabel("total time over query set (ms)")
    ax.set_title("A. Cost of the default (no BRIN)", loc="left", fontsize=10)

    ax = axes[1]
    d = rpc_reg[rpc_reg["reliable"]]
    for arm, colour in (("all_no_brin", "#4C72B0"), ("all", "#C44E52")):
        sub = d[d["arm"] == arm]
        if sub.empty:
            continue
        g = sub.groupby("random_page_cost")["misselected"].mean() * 100
        ax.plot(g.index, g.values, marker="o", label=arm, color=colour, linewidth=1.8)
    ax.invert_xaxis()
    ax.set_xlabel("random_page_cost")
    ax.set_ylabel("misselection rate (%)")
    ax.set_title("B. Misselection by planner arm", loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=8)

    fig.suptitle("Effect of random_page_cost", fontsize=11, y=1.03)
    fig.savefig(FIGURES_DIR / "fig6_random_page_cost.png")
    plt.close(fig)


def fig_brin_effect(reg: pd.DataFrame) -> None:
    """The headline: misselection with and without BRIN in the index set."""
    d = reg[reg["reliable"]]
    if d.empty or d["arm"].nunique() < 2:
        return
    fams = ["eq", "range", "ts_range", "conj"]
    fig, ax = plt.subplots(figsize=(6.0, 3.5))
    width = 0.38
    xs = np.arange(len(fams))

    for i, (arm, colour, label) in enumerate(
        (("all", "#C44E52", "with BRIN"), ("all_no_brin", "#4C72B0", "without BRIN"))
    ):
        vals = []
        for f in fams:
            sub = d[(d["arm"] == arm) & (d["family"] == f)]
            vals.append(sub["misselected"].mean() * 100 if len(sub) else 0.0)
        ax.bar(xs + (i - 0.5) * width, vals, width, label=label, color=colour)

    ax.set_xticks(xs)
    ax.set_xticklabels(fams)
    ax.set_ylabel("misselection rate (%)")
    ax.set_title(
        "Index misselection is caused by BRIN co-existence", loc="left", fontsize=10
    )
    ax.legend(frameon=False, fontsize=8)
    fig.savefig(FIGURES_DIR / "fig0_brin_effect.png")
    plt.close(fig)


def write_tables(raw: pd.DataFrame, reg: pd.DataFrame) -> None:
    summary = metrics.summarise(reg)
    if not summary.empty:
        summary.to_csv(TABLES_DIR / "table1_summary_by_dataset.csv", index=False)

    # The worst cases, which is what a reader will want to see concretely.
    d = reg[reg["reliable"]].nlargest(20, "regret")
    cols = [
        "dataset", "family", "qid", "selectivity", "true_rows", "est_rows",
        "q_error", "direction", "access_path", "best_arm", "best_access_path",
        "exec_ms_median", "best_ms", "regret",
    ]
    d[[c for c in cols if c in d.columns]].to_csv(
        TABLES_DIR / "table2_worst_regret.csv", index=False
    )

    # Which access path was chosen, by family and factor level.
    ct = pd.crosstab(
        [raw[raw["arm"] == "all"]["family"]],
        raw[raw["arm"] == "all"]["access_path"],
    )
    ct.to_csv(TABLES_DIR / "table3_access_path_choice.csv")

    reg.to_csv(TABLES_DIR / "regret_full.csv", index=False)
    raw.to_csv(TABLES_DIR / "measurements_full.csv", index=False)


def main() -> int:
    files = raw_files()
    if not files:
        print(f"No measurement files in {RAW_DIR}. Run run_experiment.py first.")
        return 1
    print("Reading: " + ", ".join(f.name for f in files))

    raw = load_raw()
    print(
        f"Loaded {len(raw)} measurements across {raw['dataset'].nunique()} datasets "
        f"and scales {sorted(raw['scale'].unique())}"
    )

    # Attach the conjunctive variant label for grouping.
    raw["variant"] = raw["params"].apply(
        lambda p: p.get("variant") if isinstance(p, dict) else None
    )

    reg = metrics.compute_regret(raw)
    print(f"Computed regret for {len(reg)} planner-choice queries")
    print(f"  reliable (best arm >= {metrics.MIN_RELIABLE_MS} ms): {int(reg['reliable'].sum())}")

    if reg["reliable"].any():
        r = reg[reg["reliable"]]
        print(f"  misselection rate: {r['misselected'].mean():.1%}")
        print(f"  median regret:     {r['regret'].median():.3f}")
        print(f"  p90 regret:        {r['regret'].quantile(0.9):.3f}")
        print(f"  max regret:        {r['regret'].max():.2f}")

    fig_brin_effect(reg)
    fig_estimation_error(reg)
    fig_regret_by_selectivity(reg)
    fig_qerror_vs_regret(reg)
    fig_extended_stats(reg)
    fig_brin_correlation(raw)
    write_tables(raw, reg)

    # random_page_cost sweep, reported separately from the scale sweep.
    rpc_path = rpc_file()
    if rpc_path:
        rpc_raw = load_raw([rpc_path])
        if not rpc_raw.empty:
            rpc_raw["variant"] = rpc_raw["params"].apply(
                lambda p: p.get("variant") if isinstance(p, dict) else None
            )
            # Regret must be computed within each cost setting, never across.
            parts = []
            for lvl, chunk in rpc_raw.groupby("random_page_cost"):
                r = metrics.compute_regret(chunk)
                r["random_page_cost"] = lvl
                parts.append(r)
            rpc_reg = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            print(f"\nrandom_page_cost sweep: {len(rpc_raw)} measurements, "
                  f"{len(rpc_reg)} planner-choice queries")
            if not rpc_reg.empty:
                rr = rpc_reg[rpc_reg["reliable"]]
                summ = rr.groupby(["random_page_cost", "arm"]).agg(
                    n=("regret", "size"),
                    misselection_rate=("misselected", "mean"),
                    regret_median=("regret", "median"),
                    regret_max=("regret", "max"),
                ).reset_index()
                summ.to_csv(TABLES_DIR / "table4_random_page_cost.csv", index=False)
                print(summ.to_string(index=False))
                rpc_reg.to_csv(TABLES_DIR / "rpc_regret_full.csv", index=False)
            fig_random_page_cost(rpc_raw, rpc_reg)

    print(f"\nFigures -> {FIGURES_DIR}")
    print(f"Tables   -> {TABLES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

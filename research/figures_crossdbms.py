"""
Cross-system figures.

The three newest findings existed only as tables. These render them.

Design constraints followed:
  * Two measures of different scale never share an axis. The degradation
    finding has a frequency and a severity axis, so it is two panels, not a
    dual-axis chart.
  * Two series, so a legend is always present, and each series also carries a
    distinct marker and line style. Identity therefore survives greyscale
    printing and colour-vision deficiency, which matters for a journal.
  * Palette validated: PostgreSQL #4C72B0 against MariaDB #C44E52 gives
    CVD dE 14.5 (protan) and 22.8 normal vision, above the required floors.
  * Grid recessive, spines minimal, no value printed on every point.

Every number is read from results/raw. Nothing is hard-coded.
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

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
import metrics  # noqa: E402
from config import FIGURES_DIR, RAW_DIR  # noqa: E402

PG = "#4C72B0"
MY = "#C44E52"
INK = "#222222"
MUTED = "#666666"

plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "font.size": 9,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.6,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": "#999999",
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
})

SCALES = {"1m": 1.0, "1250k": 1.25, "1500k": 1.5, "2000k": 2.0,
          "3000k": 3.0, "5000k": 5.0, "10m": 10.0}
COMMON_ARMS = {"none", "btree_skew", "btree_ts", "btree_a_b_separate",
               "btree_ab_composite", "all_no_brin"}


def _read(path: Path, scale: str) -> pd.DataFrame | None:
    if not path.exists():
        return None
    rows = [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    spec = pd.json_normalize(df["dataset_spec"])
    df["n_rows"] = spec["n_rows"].values
    df["scale"] = scale
    df["mrows"] = SCALES[scale]
    df["indexes_used"] = df["indexes_used"].apply(
        lambda v: ",".join(v) if isinstance(v, list) else str(v))
    df["variant"] = df["params"].apply(
        lambda p: p.get("variant") if isinstance(p, dict) else None)
    if "ext_stats" not in df:
        df["ext_stats"] = False
    df["q_error"] = [metrics.q_error(e, a)
                     for e, a in zip(df["est_rows"], df["actual_rows"])]
    return df


def load(prefix: str) -> pd.DataFrame:
    frames = []
    for tag in SCALES:
        f = _read(RAW_DIR / f"{prefix}measurements_{tag}.jsonl", tag)
        if f is not None:
            frames.append(f)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def regret_by_scale(df: pd.DataFrame, phys_only: bool = True) -> pd.DataFrame:
    d = df[df["arm"].isin(COMMON_ARMS) & (~df["ext_stats"].astype(bool))]
    if phys_only:
        d = d[d["dataset"].str.startswith("phys")]
    out = []
    for mr, chunk in d.groupby("mrows"):
        reg = metrics.compute_regret(chunk)
        reg = reg[reg["reliable"] & (reg["arm"] == "all_no_brin")]
        if len(reg):
            out.append({
                "mrows": mr,
                "misselect": reg["misselected"].mean() * 100,
                "worst": reg["regret"].max(),
                "n": len(reg),
            })
    return pd.DataFrame(out).sort_values("mrows")


def fig_degradation(pg: pd.DataFrame, my: pd.DataFrame) -> None:
    """Frequency and severity move in opposite directions in the two systems.

    Two panels, not two y-axes: the measures share no unit and a dual axis
    would invite a false visual correlation between them.
    """
    a, b = regret_by_scale(pg), regret_by_scale(my)
    if a.empty or b.empty:
        return

    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.6))

    ax = axes[0]
    ax.plot(a.mrows, a.misselect, marker="o", ls="-", color=PG, lw=1.8,
            ms=6, label="PostgreSQL")
    ax.plot(b.mrows, b.misselect, marker="s", ls="--", color=MY, lw=1.8,
            ms=6, label="MariaDB")
    ax.set_xscale("log")
    ax.set_xticks([1, 2, 3, 5, 10])
    ax.set_xticklabels(["1M", "2M", "3M", "5M", "10M"])
    ax.set_xlabel("table size (rows)")
    ax.set_ylabel("queries given a slower plan (%)")
    ax.set_title("A.  How often the optimiser errs", loc="left", fontsize=10)
    ax.set_ylim(-6, max(b.misselect.max(), a.misselect.max()) * 1.28)
    ax.legend(frameon=False, fontsize=8.5, loc="upper right")
    # Label each line at its own right-hand end, so neither annotation sits on
    # the other series. Vertical offsets are signed away from the curve.
    ax.annotate("rises with scale", xy=(10, a.misselect.iloc[-1]),
                xytext=(4.4, a.misselect.iloc[-1] - 11), fontsize=7.5,
                color=PG, ha="left")
    ax.annotate("falls with scale", xy=(10, b.misselect.iloc[-1]),
                xytext=(4.4, b.misselect.iloc[-1] - 9), fontsize=7.5,
                color=MY, ha="left")

    ax = axes[1]
    ax.plot(a.mrows, a.worst, marker="o", ls="-", color=PG, lw=1.8,
            ms=6, label="PostgreSQL")
    ax.plot(b.mrows, b.worst, marker="s", ls="--", color=MY, lw=1.8,
            ms=6, label="MariaDB")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks([1, 2, 3, 5, 10])
    ax.set_xticklabels(["1M", "2M", "3M", "5M", "10M"])
    ax.axhline(1.0, color="black", lw=0.8, ls=":")
    ax.set_xlabel("table size (rows)")
    ax.set_ylabel("worst-case slowdown (x)")
    ax.set_title("B.  How badly, at worst", loc="left", fontsize=10)
    ax.set_ylim(0.8, b.worst.max() * 2.6)
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")
    # Both labels sit low-left, clear of the suptitle and of each other.
    ax.annotate("stays under %.0fx" % np.ceil(a.worst.max()),
                xy=(10, a.worst.iloc[-1]), xytext=(1.05, 1.28),
                fontsize=7.5, color=PG)
    ax.annotate("climbs to %.0fx" % b.worst.iloc[-1],
                xy=(10, b.worst.iloc[-1]), xytext=(1.05, b.worst.max() * 1.25),
                fontsize=7.5, color=MY)

    fig.suptitle("The two systems degrade in opposite directions",
                 fontsize=11, y=1.04)
    fig.savefig(FIGURES_DIR / "fig8_degradation_shape.png")
    plt.close(fig)
    print("fig8 written from %d + %d scale points" % (len(a), len(b)))


def fig_resource_cost(pg: pd.DataFrame, my: pd.DataFrame) -> None:
    """Where each system spends its time. The profiles are inverted."""
    def costs(df):
        # Both columns must use the SAME filter or the comparison is
        # incoherent. Common arms only, extended-statistics re-runs excluded,
        # since MariaDB has no counterpart to either.
        d = df[(df["mrows"] == 10.0) & df["arm"].isin(COMMON_ARMS)
               & (~df["ext_stats"].astype(bool))]
        exec_ms = 0.0
        for r in d["runs"]:
            if isinstance(r, list) and r:
                exec_ms += sum(r)
        builds = {}
        for _, r in d.iterrows():
            b = r.get("arm_build_seconds")
            if b is not None:
                builds[(r["dataset"], r["arm"])] = b
        return exec_ms / 1000 / 60, sum(builds.values()) / 60

    pe, pb = costs(pg)
    me, mb = costs(my)
    if pe == 0 or me == 0:
        return

    fig, ax = plt.subplots(figsize=(6.6, 3.8))
    x = np.arange(2)
    w = 0.34
    gap = 0.03  # visible surface gap between paired bars
    r1 = ax.bar(x - w / 2 - gap, [pe, pb], w, color=PG, label="PostgreSQL")
    r2 = ax.bar(x + w / 2 + gap, [me, mb], w, color=MY, label="MariaDB")
    for rects in (r1, r2):
        ax.bar_label(rects, fmt="%.0f min", fontsize=8.5, padding=3, color=INK)

    ax.set_yscale("log")
    ax.set_ylim(10, me * 4.2)  # headroom so labels clear the title
    ax.set_xticks(x)
    ax.set_xticklabels(["running queries", "building indexes"])
    ax.set_ylabel("total time (minutes, log scale)")
    ax.set_title("Where each system spends its time, 10M rows",
                 loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=8.5, loc="upper right")
    # Ratio labels sit between the bar pairs, inside the axes, never near the
    # title.
    ax.annotate("%.1fx slower" % (me / pe), xy=(0, 0), xytext=(0, pe * 0.42),
                fontsize=8.5, color=MY, ha="center")
    ax.annotate("%.1fx faster" % (pb / mb), xy=(1, 0), xytext=(1, mb * 0.55),
                fontsize=8.5, color=MY, ha="center")
    fig.savefig(FIGURES_DIR / "fig9_resource_cost.png")
    plt.close(fig)
    print("fig9: pg exec %.1f build %.1f | my exec %.1f build %.1f"
          % (pe, pb, me, mb))


def fig_cross_conj(pg: pd.DataFrame, my: pd.DataFrame) -> None:
    """Both systems fail on correlated predicates, but at different points."""
    def series(df):
        d = df[(df["mrows"] == 1.0) & (df["family"] == "conj")
               & (df["arm"] == "all_no_brin")
               & (~df["ext_stats"].astype(bool))
               & (df["variant"] == "dependent")]
        d = d[d["dataset"].str.startswith("dep")]
        if d.empty:
            return None
        g = d.groupby(d["dataset_spec"].apply(lambda s: s["dep_strength"]))
        # raw_access_type exists only in the MariaDB records; PostgreSQL
        # reports access_path instead.
        col = "raw_access_type" if "raw_access_type" in d.columns else "access_path"
        return g["q_error"].median(), g[col].agg(
            lambda x: x.mode().iloc[0] if len(x.mode()) else "")

    p = series(pg)
    m = series(my)
    if p is None or m is None:
        return
    pq, _ = p
    mq, mtype = m

    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    ax.plot(pq.index, pq.values, marker="o", ls="-", color=PG, lw=1.8,
            ms=6, label="PostgreSQL")
    ax.plot(mq.index, mq.values, marker="s", ls="--", color=MY, lw=1.8,
            ms=6, label="MariaDB")
    ax.axhline(1.0, color="black", lw=0.9, ls=":")
    ax.text(0.02, 1.06, "perfect estimate", fontsize=7.5, color=MUTED,
            transform=ax.get_yaxis_transform())
    ax.set_yscale("log")
    ax.set_xlabel("strength of correlation between the two columns")
    ax.set_ylabel("estimation error (x wrong, log scale)")
    ax.set_title("Both systems fail once two indexes are merged",
                 loc="left", fontsize=10)
    ax.legend(frameon=False, fontsize=8.5, loc="center left")

    # Mark the point where MariaDB abandons the composite index. That switch
    # is the whole finding, so it is labelled rather than left to the reader.
    switch = [k for k in mtype.index if "merge" in str(mtype[k])]
    if switch:
        k = min(switch)
        # Placed below the spike, not above it: above collides with the title.
        ax.annotate("MariaDB switches to\nindex_merge here",
                    xy=(k, mq[k] * 0.85), xytext=(k - 0.30, mq[k] * 0.13),
                    fontsize=7.5, color=MY, ha="center",
                    arrowprops=dict(arrowstyle="->", lw=0.8, color=MY))
        ax.annotate("exact while one\nindex answers it",
                    xy=(0.42, 1.0), xytext=(0.30, 2.4),
                    fontsize=7.5, color=MY, ha="left")
    fig.savefig(FIGURES_DIR / "fig10_cross_conj.png")
    plt.close(fig)
    print("fig10 written; MariaDB switch at dep=%s" % (min(switch) if switch else "n/a"))


def main() -> int:
    pg = load("")
    my = load("mysql_")
    print("loaded postgresql=%d mariadb=%d" % (len(pg), len(my)))
    if pg.empty or my.empty:
        print("missing data for one system")
        return 1
    fig_degradation(pg, my)
    fig_resource_cost(pg, my)
    fig_cross_conj(pg, my)
    print("figures -> %s" % FIGURES_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Static charts (PNG) for the PDF report — same content as the HTML charts."""
from __future__ import annotations

import io
from itertools import pairwise
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID = "#17201c", "#737d78", "#eceeec"
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": "#dfe3e0", "axes.labelcolor": MUTED,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "axes.axisbelow": True, "figure.dpi": 200,
})


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _hist(ax, h: dict, color: str, markers: list[tuple[float, str]], xlabel: str, xfmt=None) -> None:
    edges, counts = h["edges"], h["counts"]
    total = sum(counts) or 1
    widths = [b - a for a, b in pairwise(edges)]
    ax.bar(edges[:-1], [c / total * 100 for c in counts], width=[w * 0.9 for w in widths], align="edge", color=color)
    top = max(counts) / total * 100
    lo, hi = edges[0], edges[-1]
    for i, (x, label) in enumerate(markers):
        ax.axvline(x, color=INK, lw=1.1, ls="--" if i == 1 else "-")
        right_side = (x - lo) / (hi - lo) < 0.7          # label beside the line, never on it
        ax.annotate(label, (x, top * (1.3 - 0.12 * i)), xytext=(4 if right_side else -4, 0),
                    textcoords="offset points", ha="left" if right_side else "right", va="center",
                    fontsize=7.5, color=INK, fontweight="bold")
    ax.set_ylim(0, top * 1.42)
    ax.set_ylabel("share of days (%)")
    ax.set_xlabel(xlabel)
    ax.grid(axis="x", visible=False)
    if xfmt:
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: xfmt(v)))


def build(d: dict[str, Any]) -> dict[str, bytes]:
    k = d["kpi"]
    out: dict[str, bytes] = {}
    keur = lambda v: f"€{v / 1000:,.0f}k"

    # service bands
    fig, ax = plt.subplots(figsize=(7.2, 0.75))
    bands = d["service_bands"]
    names = {"100%": "✓ all on time", "95-100%": "! 95–100% on time", "90-95%": "! 90–95% on time",
             "<90%": "✕ below 90% on time"}
    left = 0.0
    for (key, v), col in zip(bands.items(), STATUS.values()):
        ax.barh(0, v, left=left, color=col, height=0.5, edgecolor="white", linewidth=1.5,
                label=f"{names[key]}: {v * 100:.1f}%")
        left += v
    ax.set_xlim(0, 1)
    ax.axis("off")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.05), ncol=4, frameon=False, fontsize=7.5,
              handlelength=1, columnspacing=1.2)
    out["service"] = _png(fig)

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
    _hist(axes[0], d["hist"]["v_required"], BLUE,
          [(k["v_required_p50"], f"median {k['v_required_p50']:,.0f}"), (k["v_use_mean"], f"in service {k['v_use_mean']:,.0f}"),
           (k["v_required_p95"], f"P95 {k['v_required_p95']:,.0f}")], "vehicles required")
    _hist(axes[1], d["hist"]["backlog"], ORANGE, [(k["backlog_p95"], f"P95 {k['backlog_p95']:,.0f}")],
          "open deliveries at end of shift")
    fig.tight_layout()
    out["vehicles"] = _png(fig)

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
    _hist(axes[0], d["hist"]["total_cost"], BLUE,
          [(k["total_cost_mean"], f"mean {keur(k['total_cost_mean'])}"), (k["total_cost_p95"], f"P95 {keur(k['total_cost_p95'])}")],
          "variable cost per day", keur)
    _hist(axes[1], d["hist"]["maint_cost"], BLUE,
          [(k["maint_cost_mean"], f"mean {keur(k['maint_cost_mean'])}"), (k["maint_cost_p95"], f"P95 {keur(k['maint_cost_p95'])}")],
          "maintenance cost per day", keur)
    fig.tight_layout()
    out["cost"] = _png(fig)

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
    pmf = d["breakdowns_pmf"]
    edges = [i - 0.5 for i in range(len(pmf) + 1)]
    _hist(axes[0], {"edges": edges, "counts": pmf}, BLUE,
          [(k["breakdowns_mean"], f"mean {k['breakdowns_mean']:.1f}"), (19.5, f"≥ 20: {k['p_breakdowns_ge_20'] * 100:.1f}%")],
          "breakdowns per day")
    _hist(axes[1], d["hist"]["fuel_l"], ORANGE,
          [(k["fuel_l_mean"], f"mean {k['fuel_l_mean']:,.0f} L"), (k["fuel_l_p95"], f"P95 {k['fuel_l_p95']:,.0f} L")],
          "litres of diesel per day")
    fig.tight_layout()
    out["breakdowns_fuel"] = _png(fig)

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))
    for ax, key, title in ((axes[0], "shortfall", "Drivers of late deliveries"), (axes[1], "cost", "Drivers of daily cost")):
        items = sorted(d["sensitivity"].items(), key=lambda kv: abs(kv[1][key]))
        vals = [v[key] for _, v in items]
        ax.barh([n for n, _ in items], vals, color=[ORANGE if v >= 0 else BLUE for v in vals], height=0.6)
        for i, v in enumerate(vals):
            ax.text(v + (0.03 if v >= 0 else -0.03), i, f"{v:.2f}", va="center", ha="left" if v >= 0 else "right",
                    fontsize=8, color=INK)
        ax.set_xlim(-1, 1)
        ax.axvline(0, color="#dfe3e0", lw=1)
        ax.set_title(title, fontsize=9, color=INK, loc="left")
        ax.set_xlabel("Spearman ρ")
        ax.grid(axis="y", visible=False)
    fig.tight_layout()
    out["sensitivity"] = _png(fig)

    sz = d["sizing"]
    fig, ax = plt.subplots(figsize=(7.2, 2.8))
    ax.plot(sz["vehicles"], [p * 100 for p in sz["drivers_fixed"]], "-o", color=BLUE, ms=4, lw=2,
            label="driver pool stays at 520")
    ax.plot(sz["vehicles"], [p * 100 for p in sz["drivers_scaled"]], "-o", color=ORANGE, ms=4, lw=2,
            label="driver pool grows with the fleet (1.1 per vehicle)")
    ax.axhline(90, color=MUTED, ls="--", lw=1)
    ax.text(sz["vehicles"][0], 91, "example target 90%", fontsize=8, color=INK)
    ax.set_ylim(50, 100)
    ax.set_xlabel("available vehicles")
    ax.set_ylabel("fully on-time days (%)")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    out["sizing"] = _png(fig)

    cv = d["convergence"]
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    ax.fill_between(cv["n"], [v * 100 for v in cv["lo"]], [v * 100 for v in cv["hi"]], color=BLUE, alpha=0.2,
                    label="95% confidence interval")
    ax.plot(cv["n"], [v * 100 for v in cv["p"]], color=BLUE, lw=2, label="share of fully on-time days")
    ax.set_xscale("log")
    ax.set_xlabel("number of scenarios (log scale)")
    ax.set_ylabel("%")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    out["convergence"] = _png(fig)
    return out

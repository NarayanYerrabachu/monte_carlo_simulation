"""PDF and Excel reports for a finished job (fleet simulation or loop test).

Built only from the job's stored result — the same JSON the live viewer and the
report page show — so every format says the same thing.
"""
from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
)

from mc_service.fleet import (
    charts as fleet_charts,
)
from mc_service.fleet.pdf import S, _footer, _img, _section, _table_style

BLUE, ORANGE, GREEN, RED, INK = fleet_charts.BLUE, fleet_charts.ORANGE, fleet_charts.AQUA, "#d03b3b", fleet_charts.INK
HEAD_FILL, HEAD_FONT = PatternFill("solid", fgColor="1C5CAB"), Font(bold=True, color="FFFFFF")


def pct(v: float | None, d: int = 1) -> str:
    return "–" if v is None else f"{v * 100:.{d}f}%"


def num(v: float | None, d: int = 0) -> str:
    return "–" if v is None else f"{v:,.{d}f}"


def eur(v: float | None) -> str:
    return "–" if v is None else f"€{v:,.0f}"


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _bars(ax, h: dict, color: str, lines: list[tuple[float, str, str]], xlabel: str) -> None:
    edges, counts = h["edges"], h["counts"]
    total = sum(counts) or 1
    width = (edges[1] - edges[0]) * 0.9 if len(edges) > 1 else 1
    ax.bar(edges[:-1], [c / total for c in counts], width=width, align="edge", color=color)
    top = max(counts) / total if counts else 1
    for i, (x, label, col) in enumerate(lines):
        ax.axvline(x, color=col, lw=1.3, ls="--")
        ax.annotate(label, (x, top * (1.25 - 0.12 * i)), xytext=(4, 0), textcoords="offset points",
                    fontsize=7.5, color=col, fontweight="bold", va="center")
    ax.set_ylim(0, top * 1.35)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("probability")
    ax.grid(axis="x", visible=False)


# ── fleet ───────────────────────────────────────────────────────────────────
def _fleet_charts(f: dict) -> dict[str, bytes]:
    k, c, s = f["summary"]["kpi"], f["config"], f["summary"]
    out = {}
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.7))
    dt = s["delivery_time"]
    if dt["share"]:
        mids = [(a + b) / 2 for a, b in zip(dt["edges"], dt["edges"][1:], strict=False)]
        axes[0].bar(mids, dt["share"], width=(dt["edges"][1] - dt["edges"][0]) * 0.9,
                    color=[BLUE if m <= c["delivery_target_h"] else "#b9c0cb" for m in mids])
        axes[0].axvline(c["delivery_target_h"], color=RED, ls="--", lw=1.3)
        axes[0].set_title(f"Delivery time · P(within {c['delivery_target_h']} h | delivered) = {pct(dt['p_within_target'])}",
                          fontsize=8, color=INK, loc="left")
        axes[0].set_xlabel("delivery time (hours)")
        axes[0].set_ylabel("probability")
    o = s["outcome"]
    axes[1].pie([o["on_time"], o["delayed"]], labels=["On time", "Delayed"], colors=[GREEN, RED],
                autopct="%1.1f%%", startangle=90, counterclock=False, wedgeprops={"width": 0.45},
                textprops={"fontsize": 8})
    axes[1].set_title("Delivery outcome", fontsize=8, color=INK)
    fig.tight_layout()
    out["delivery"] = _png(fig)

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.7))
    _bars(axes[0], s["hist"]["vehicles_required"], BLUE,
          [(k["vehicles_required_mean"], f"expected {num(k['vehicles_required_mean'])}", ORANGE),
           (k["vehicles_required_p95"], f"P95 {num(k['vehicles_required_p95'])}", RED),
           (k["fleet_size"], f"fleet {num(k['fleet_size'])}", GREEN)], "vehicles required")
    _bars(axes[1], s["hist"]["maint_cost"], GREEN,
          [(k["maint_cost_mean"], f"expected {eur(k['maint_cost_mean'])}", ORANGE),
           (k["maint_cost_p95"], f"P95 {eur(k['maint_cost_p95'])}", RED)], "maintenance cost per day (€)")
    fig.tight_layout()
    out["dists"] = _png(fig)

    cv = s["convergence"]
    fig, ax = plt.subplots(figsize=(7.2, 2.2))
    ax.fill_between(cv["n"], cv["lo"], cv["hi"], color=ORANGE, alpha=0.2, label="95% confidence interval")
    ax.plot(cv["n"], cv["p"], color=ORANGE, lw=2, label="P(meet SLA)")
    ax.set_xscale("log")
    ax.set_xlabel("simulated days (log scale)")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    out["convergence"] = _png(fig)
    return out


def _fleet_story(f: dict, job: dict) -> list:
    k, c, s = f["summary"]["kpi"], f["config"], f["summary"]
    png = _fleet_charts(f)
    inp = s.get("inputs") or {}
    ctx = s.get("context") or {}
    r = lambda q, fmt: "–" if not q else f"{fmt(q['p5'])} – {fmt(q['p95'])}"
    story: list = [
        Paragraph("FLEET MANAGEMENT · MONTE CARLO ON TDA / ML RESULTS", S["eyebrow"]),
        Paragraph("Monte Carlo Simulation in Fleet Management", S["title"]),
        Spacer(1, 4),
        Paragraph(f"Dataset {job['dataset_id']} · job {job['job_id']} · {num(f['n_completed'])} simulated operating days · "
                  f"generated {datetime.now(UTC):%d %b %Y}", S["small"]),
    ]
    outputs = [
        [pct(k["p_meet_sla"]), f"probability a day meets the on-time SLA (≥ {pct(c['sla_on_time'], 0)} on time)"],
        [pct(k["p_miss_sla"]), "probability of missing the delivery target"],
        [f"{num(k['vehicles_required_mean'])} vehicles", f"expected to meet demand (P95 {num(k['vehicles_required_p95'])}; fleet {num(k['fleet_size'])})"],
        [pct(k["p_delivery_within_target"]), f"of planned deliveries within {c['delivery_target_h']} h"],
        [pct(k["fleet_availability_mean"]), "fleet availability (driver present, no breakdown)"],
        [f"{num(k['fuel_l_mean'])} L", f"expected daily fuel (P95 {num(k['fuel_l_p95'])} L) · cost {eur(k['fuel_cost_mean'])}"],
        [pct(k["p_breakdowns_over_alert"]), f"probability of more than {k['breakdown_alert']} breakdowns in a day"],
        [eur(k["maint_cost_mean"]), f"expected maintenance cost per day (P95 {eur(k['maint_cost_p95'])})"],
    ]
    t = Table([[Paragraph(f"<b>{a}</b>", S["cell"]), Paragraph(b, S["cell"])] for a, b in outputs],
              colWidths=[3.2 * cm, 13.8 * cm])
    t.setStyle(_table_style())
    story += _section("Result", "Probabilistic outputs") + [t]

    rows = [["Input (observed)", "P5 – P95"],
            ["Vehicles · days", f"{num(inp.get('vehicles'))} · {num(inp.get('days'))}"],
            ["Daily demand", r(inp.get("daily_demand"), num)],
            ["Driver availability", r(inp.get("driver_availability"), lambda v: pct(v, 0))],
            ["Breakdowns per day", r(inp.get("daily_breakdowns"), num)],
            ["Fuel consumption (L/100 km)", r(inp.get("fuel_l_per_100km"), lambda v: num(v, 1))],
            ["Route duration (h)", r(inp.get("route_duration_h"), lambda v: num(v, 1))],
            ["Fuel price (€/L)", r(inp.get("fuel_price_eur_l"), lambda v: num(v, 2))],
            ["Maintenance per breakdown", r(inp.get("maintenance_cost_per_breakdown"), eur)]]
    ti = Table(rows, colWidths=[8.5 * cm, 8.5 * cm])
    ti.setStyle(_table_style())
    story += _section("Model", "Uncertain inputs, as observed in the data",
                      f"Each simulated day draws a real historical day and resamples {num(c['fleet_size'])} vehicle records "
                      f"from it ({num(c['n_pool'])} of {num(c['n_records'])} records in the pool). Demand stays at the "
                      "historical level; a vehicle's observed on-time deliveries are its capacity.") + [ti]

    story += [KeepTogether(_section("Charts", "Distributions") + [_img(png["delivery"]), _img(png["dists"])])]

    reg = [["Regime (TDA cluster)", "Share", "On time", "Breakdown", "Availability", "Late share"]] + [
        [Paragraph(g["label"], S["cell"]), pct(g["share"]), pct(g["on_time_share"]), pct(g["breakdown_rate"]),
         pct(g["availability"]), pct(g["late_deliveries_share"])]
        for g in sorted(f["results"], key=lambda g: -g["late_deliveries_share"])]
    tr = Table(reg, colWidths=[6.2 * cm, 2 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm], repeatRows=1)
    tr.setStyle(_table_style())
    story += [KeepTogether(_section("TDA", "Where the risk comes from — TDA regimes") + [tr])]

    notes = []
    if ctx:
        notes.append(f"TDA / ML: {num(ctx.get('n_clusters'))} clusters, {num(ctx.get('n_noise'))} noise records, "
                     f"{num(ctx.get('n_anomalies_high'))} records scored HIGH by the anomaly model.")
        for title, items in (("Relationships", ctx.get("relationships")), ("Patterns", ctx.get("patterns"))):
            if items:
                notes.append(f"{title}: " + "; ".join(items))
    a = s["anomalies"]
    notes.append("Anomaly exclusion off: all records used." if a["threshold"] is None else
                 f"{num(a['excluded'])} operating records with anomaly score ≥ {a['threshold']} excluded; "
                 f"{num(a.get('kept_events'))} flagged breakdown / absence records kept.")
    story += _section("Context", "TDA / ML findings and data quality") + [Paragraph(n, S["body"]) for n in notes]
    story += [KeepTogether(_section("Quality", "Are the scenarios enough?") + [_img(png["convergence"])])]
    return story


def _fleet_xlsx(f: dict, job: dict, wb: Workbook) -> None:
    k, c, s = f["summary"]["kpi"], f["config"], f["summary"]
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Monte Carlo Simulation in Fleet Management"
    ws["A1"].font = Font(bold=True, size=16)
    ws["A2"] = f"Dataset {job['dataset_id']} · job {job['job_id']} · {f['n_completed']:,} simulated days"
    _head(ws, 4, ["Metric", "Value"])
    rows = [("P(day meets SLA)", k["p_meet_sla"], "0.0%"), ("P(miss target)", k["p_miss_sla"], "0.0%"),
            ("SLA (on-time share)", c["sla_on_time"], "0%"), ("Delivery target (h)", c["delivery_target_h"], "0.0"),
            ("Delivered within target (of planned)", k["p_delivery_within_target"], "0.0%"),
            ("Fleet size", k["fleet_size"], "#,##0"), ("Vehicles required (mean)", k["vehicles_required_mean"], "#,##0.0"),
            ("Vehicles required (P95)", k["vehicles_required_p95"], "#,##0"),
            ("Fleet availability", k["fleet_availability_mean"], "0.0%"),
            ("Fuel per day (L, mean)", k["fuel_l_mean"], "#,##0"), ("Fuel per day (L, P95)", k["fuel_l_p95"], "#,##0"),
            ("Fuel cost per day (mean)", k["fuel_cost_mean"], "€#,##0"),
            ("Breakdowns per day (mean)", k["breakdowns_mean"], "#,##0.0"),
            (f"P(> {k['breakdown_alert']} breakdowns)", k["p_breakdowns_over_alert"], "0.0%"),
            ("Maintenance cost per day (mean)", k["maint_cost_mean"], "€#,##0"),
            ("Maintenance cost per day (P95)", k["maint_cost_p95"], "€#,##0")]
    for i, (name, value, fmt) in enumerate(rows, 5):
        ws.cell(row=i, column=1, value=name)
        ws.cell(row=i, column=2, value=value).number_format = fmt
    _widths(ws, [44, 16])

    ws = wb.create_sheet("Inputs (observed)")
    _head(ws, 1, ["Input", "P5", "Median", "P95"])
    for i, (key, q) in enumerate(((k2, v) for k2, v in (s.get("inputs") or {}).items() if isinstance(v, dict)), 2):
        ws.cell(row=i, column=1, value=key.replace("_", " "))
        for j, part in enumerate(("p5", "median", "p95"), 2):
            ws.cell(row=i, column=j, value=q[part]).number_format = "#,##0.00"
    _widths(ws, [34, 14, 14, 14])

    ws = wb.create_sheet("TDA regimes")
    cols = ["regime", "label", "records", "share", "on_time_share", "within_target_share", "breakdown_rate",
            "availability", "fuel_l_mean", "maint_cost_mean", "late_deliveries_share"]
    _head(ws, 1, [col.replace("_", " ") for col in cols])
    for i, g in enumerate(f["results"], 2):
        for j, col in enumerate(cols, 1):
            cell = ws.cell(row=i, column=j, value=g[col])
            if col.endswith(("share", "rate")) or col == "availability":
                cell.number_format = "0.0%"
    _widths(ws, [8, 36] + [14] * (len(cols) - 2))

    ws = wb.create_sheet("Distributions")
    col = 1
    for key, title in (("vehicles_required", "Vehicles required"), ("maint_cost", "Maintenance cost per day"),
                       ("fuel_l", "Fuel per day (L)"), ("on_time_share", "On-time share per day")):
        h = s["hist"][key]
        total = sum(h["counts"]) or 1
        ws.cell(row=1, column=col, value=title).font = Font(bold=True)
        _head(ws, 2, ["From", "To", "Share"], col)
        for i, (a, b, cnt) in enumerate(zip(h["edges"], h["edges"][1:], h["counts"], strict=False), 3):
            ws.cell(row=i, column=col, value=a)
            ws.cell(row=i, column=col + 1, value=b)
            ws.cell(row=i, column=col + 2, value=cnt / total).number_format = "0.0%"
        chart = BarChart()
        chart.title, chart.legend, chart.gapWidth = title, None, 20
        chart.add_data(Reference(ws, min_col=col + 2, min_row=2, max_row=2 + len(h["counts"])), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=col, min_row=3, max_row=2 + len(h["counts"])))
        chart.height, chart.width = 7, 13
        ws.add_chart(chart, f"{get_column_letter(col)}{len(h['counts']) + 5}")
        col += 4

    ws = wb.create_sheet("Delivery time")
    dt = s["delivery_time"]
    _head(ws, 1, ["From (h)", "To (h)", "Share of deliveries"])
    for i, (a, b, v) in enumerate(zip(dt["edges"], dt["edges"][1:], dt["share"], strict=False), 2):
        ws.cell(row=i, column=1, value=a)
        ws.cell(row=i, column=2, value=b)
        ws.cell(row=i, column=3, value=v).number_format = "0.0%"
    _widths(ws, [10, 10, 20])

    _convergence_sheet(wb, s["convergence"], "P(meet SLA)")
    ctx = s.get("context")
    if ctx:
        ws = wb.create_sheet("TDA-ML context")
        row = 1
        for key, value in ctx.items():
            ws.cell(row=row, column=1, value=key.replace("_", " ")).font = Font(bold=True)
            ws.cell(row=row, column=2, value="; ".join(map(str, value)) if isinstance(value, list) else
                    (", ".join(f"{a}={b}" for a, b in value.items()) if isinstance(value, dict) else value))
            ws.cell(row=row, column=2).alignment = Alignment(wrap_text=True)
            row += 1
        _widths(ws, [22, 100])


# ── loops ───────────────────────────────────────────────────────────────────
def _loops_story(lp: dict, job: dict) -> list:
    s, c = lp["summary"], lp["config"]
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    h = s["null_hist"]
    if h["counts"]:
        _bars(ax, h, ORANGE, [(s["top_persistence"], f"observed {s['top_persistence']:.3f}", BLUE),
                              (s["noise_band"], f"{pct(1 - c['alpha'], 0)} band {s['noise_band']:.3f}", "#7c3aed")],
              "longest H1 persistence in random data")
    png = _png(fig)
    rows = [["#", "Birth", "Death", "Persistence", "p-value", "Verdict"]] + [
        [r["index"] + 1, f"{r['birth']:.4f}", f"{r['death']:.4f}", f"{r['persistence']:.4f}",
         f"{r['p_value']:.4f}", "significant" if r["significant"] else "noise"] for r in lp["results"][:20]]
    t = Table(rows, colWidths=[1.2 * cm, 3 * cm, 3 * cm, 3.2 * cm, 3 * cm, 3.2 * cm], repeatRows=1)
    t.setStyle(_table_style())
    return [
        Paragraph("TOPOLOGY · MONTE CARLO SIGNIFICANCE", S["eyebrow"]),
        Paragraph("Are the loops real?", S["title"]),
        Paragraph(f"Dataset {job['dataset_id']} · job {job['job_id']} · {num(lp['n_completed'])} simulations · "
                  f"null model {c['null']} · {num(c['sample_n'])} sampled points · generated {datetime.now(UTC):%d %b %Y}",
                  S["small"]),
        *_section("Result", f"{s['n_significant']} of {s['n_loops_observed']} loops are significant (p ≤ {c['alpha']})",
                  "Each loop is compared with the longest loop found in random data of the same shape; a loop is "
                  "significant when random data rarely produces one that persists as long."),
        _img(png), Spacer(1, 6), t,
    ]


def _loops_xlsx(lp: dict, job: dict, wb: Workbook) -> None:
    s, c = lp["summary"], lp["config"]
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Monte Carlo significance of H1 loops"
    ws["A1"].font = Font(bold=True, size=16)
    ws["A2"] = f"Dataset {job['dataset_id']} · job {job['job_id']}"
    for i, (name, value) in enumerate([("Simulations", lp["n_completed"]), ("Null model", c["null"]),
                                       ("Sample size", c["sample_n"]), ("Alpha", c["alpha"]),
                                       ("Loops observed", s["n_loops_observed"]), ("Significant loops", s["n_significant"]),
                                       ("Longest loop persistence", s["top_persistence"]),
                                       ("Noise band", s["noise_band"])], 4):
        ws.cell(row=i, column=1, value=name)
        ws.cell(row=i, column=2, value=value)
    _widths(ws, [30, 18])
    ws = wb.create_sheet("Loops")
    _head(ws, 1, ["#", "Birth", "Death", "Persistence", "p-value", "Significant"])
    for i, r in enumerate(lp["results"], 2):
        for j, v in enumerate([r["index"] + 1, r["birth"], r["death"], r["persistence"], r["p_value"], r["significant"]], 1):
            ws.cell(row=i, column=j, value=v)
    _widths(ws, [6, 12, 12, 14, 12, 12])


# ── shared ──────────────────────────────────────────────────────────────────
def _head(ws, row: int, labels: list[str], col: int = 1) -> None:
    for i, label in enumerate(labels):
        cell = ws.cell(row=row, column=col + i, value=label)
        cell.fill, cell.font = HEAD_FILL, HEAD_FONT


def _widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _convergence_sheet(wb: Workbook, cv: dict, label: str) -> None:
    ws = wb.create_sheet("Convergence")
    _head(ws, 1, ["Simulations", label, "95% CI lower", "95% CI upper"])
    for i, row in enumerate(zip(cv["n"], cv["p"], cv["lo"], cv["hi"], strict=False), 2):
        ws.cell(row=i, column=1, value=row[0])
        for j, v in enumerate(row[1:], 2):
            ws.cell(row=i, column=j, value=v).number_format = "0.0%"
    chart = LineChart()
    chart.title = "Estimate as simulations grow"
    chart.add_data(Reference(ws, min_col=2, max_col=4, min_row=1, max_row=1 + len(cv["n"])), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=1 + len(cv["n"])))
    chart.height, chart.width = 8, 16
    ws.add_chart(chart, "F2")
    _widths(ws, [12, 18, 14, 14])


def kind(result: dict[str, Any]) -> str:
    if result.get("fleet"):
        return "fleet"
    if result.get("loops"):
        return "loops"
    raise ValueError("This job has no fleet or loop result to report on.")


def build_pdf(result: dict[str, Any]) -> bytes:
    job = {"dataset_id": result["dataset_id"], "job_id": result["job_id"]}
    story = _fleet_story(result["fleet"], job) if kind(result) == "fleet" else _loops_story(result["loops"], job)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=2 * cm, title="Monte Carlo report", author="CortXplorer Monte Carlo Service")
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def build_xlsx(result: dict[str, Any]) -> bytes:
    job = {"dataset_id": result["dataset_id"], "job_id": result["job_id"]}
    wb = Workbook()
    if kind(result) == "fleet":
        _fleet_xlsx(result["fleet"], job, wb)
    else:
        _loops_xlsx(result["loops"], job, wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

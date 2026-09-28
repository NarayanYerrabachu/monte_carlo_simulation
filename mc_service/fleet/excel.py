"""Excel export of the fleet report: summary, inputs, distributions, drivers,
what-if, convergence (each with a native Excel chart) and every scenario."""
from __future__ import annotations

import io
from datetime import UTC, datetime

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from mc_service.fleet.model import LIMITATIONS, NEXT_STEPS, FleetReport

HEAD_FILL = PatternFill("solid", fgColor="1C5CAB")
HEAD_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=16)
H2_FONT = Font(bold=True, size=12)
WRAP = Alignment(wrap_text=True, vertical="top")

PCT, EUR, INT, DEC = "0.0%", "€#,##0", "#,##0", "#,##0.0"

# scenario columns: (column, header, number format)
SCENARIO_COLUMNS = [
    ("v_avail", "Available vehicles", INT), ("p_break", "Breakdown probability", "0.00%"),
    ("breakdowns", "Breakdowns", INT), ("driver_rate", "Driver availability", "0.0%"),
    ("drivers", "Drivers present", INT), ("v_use", "Vehicles in service", INT),
    ("demand", "Demand (deliveries)", INT), ("stop_min", "Minutes per delivery", DEC),
    ("traffic_min", "Traffic delay (min)", DEC), ("capacity", "Capacity (deliveries)", INT),
    ("backlog", "Backlog (deliveries)", INT), ("service", "Delivered in shift", PCT),
    ("v_required", "Vehicles required", INT), ("km", "Distance (km)", INT), ("fuel_l", "Fuel (L)", INT),
    ("fuel_price", "Fuel price (€/L)", "€0.00"), ("fuel_cost", "Fuel cost", EUR),
    ("maint_cost", "Maintenance cost", EUR), ("downtime_h", "Workshop hours", DEC),
    ("overtime_h", "Overtime hours", DEC), ("overtime_cost", "Overtime cost", EUR),
    ("total_cost", "Variable cost", EUR),
]


def _header(ws, row: int, labels: list[str], col: int = 1) -> None:
    for i, label in enumerate(labels):
        c = ws.cell(row=row, column=col + i, value=label)
        c.fill, c.font = HEAD_FILL, HEAD_FONT


def _widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def build_xlsx(report: FleetReport) -> bytes:
    d, k = report.data, report.data["kpi"]
    wb = Workbook()

    # ── Summary ─────────────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Monte Carlo Simulation in Fleet Management"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"{d['n']:,} simulated operating days · seed {d['seed']} · generated {datetime.now(UTC):%d %b %Y}"
    _header(ws, 4, ["Metric", "Value", "Unit / note"])
    metrics = [
        ("Operating days without backlog", k["p_day_on_time"], PCT, "share of simulated days"),
        ("  95% CI lower", k["p_day_on_time_ci"][0], PCT, ""),
        ("  95% CI upper", k["p_day_on_time_ci"][1], PCT, ""),
        ("Deliveries within the shift (mean)", k["service_mean"], PCT, ""),
        ("Days below 95% on time", k["p_service_below_95"], PCT, ""),
        ("Backlog (mean)", k["backlog_mean"], INT, "deliveries"),
        ("Backlog (95th percentile)", k["backlog_p95"], INT, "deliveries"),
        ("Vehicles required (median)", k["v_required_p50"], INT, "vehicles"),
        ("Vehicles required (95th percentile)", k["v_required_p95"], INT, "vehicles"),
        ("Vehicles in service (mean)", k["v_use_mean"], INT, "vehicles"),
        ("Days needing more vehicles than in service", k["p_required_gt_available"], PCT, ""),
        ("Breakdowns per day (mean)", k["breakdowns_mean"], DEC, "vehicles"),
        ("P(≥ 20 breakdowns)", k["p_breakdowns_ge_20"], PCT, ""),
        ("Workshop hours per day (mean)", k["downtime_h_mean"], DEC, "hours"),
        ("Fuel per day (mean)", k["fuel_l_mean"], INT, "litres"),
        ("Fuel per day (95th percentile)", k["fuel_l_p95"], INT, "litres"),
        ("Fuel cost per day (mean)", k["fuel_cost_mean"], EUR, ""),
        ("Maintenance cost per day (mean)", k["maint_cost_mean"], EUR, ""),
        ("Maintenance cost per day (95th percentile)", k["maint_cost_p95"], EUR, ""),
        ("Cost per breakdown (mean)", d["cost_per_breakdown"], EUR, ""),
        ("Overtime cost per day (mean)", k["overtime_cost_mean"], EUR, ""),
        ("Variable cost per day (mean)", k["total_cost_mean"], EUR, ""),
        ("Variable cost per day (5th percentile)", k["total_cost_p5"], EUR, ""),
        ("Variable cost per day (95th percentile)", k["total_cost_p95"], EUR, ""),
    ]
    for i, (name, value, fmt, note) in enumerate(metrics, 5):
        ws.cell(row=i, column=1, value=name)
        c = ws.cell(row=i, column=2, value=value)
        c.number_format = fmt
        ws.cell(row=i, column=3, value=note)
    row = 5 + len(metrics) + 1
    for title, body in (("Recommendations", [f"{i}. {r['title']}: {r['text']}" for i, r in enumerate(d["recommendations"], 1)]),
                        ("Model limitations", LIMITATIONS), ("Next steps", NEXT_STEPS)):
        ws.cell(row=row, column=1, value=title).font = H2_FONT
        row += 1
        for line in body:
            ws.cell(row=row, column=1, value=line).alignment = WRAP
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
            ws.row_dimensions[row].height = 48
            row += 1
        row += 1
    _widths(ws, [46, 16, 60])

    # ── Inputs ──────────────────────────────────────────────────────────────
    ws = wb.create_sheet("Inputs")
    _header(ws, 1, ["Input", "Distribution", "Range", "Source"])
    for i, r in enumerate(d["inputs"], 2):
        for j, key in enumerate(("input", "distribution", "range", "source"), 1):
            ws.cell(row=i, column=j, value=r[key]).alignment = WRAP
    _widths(ws, [46, 44, 26, 34])
    ws.freeze_panes = "A2"

    # ── Distributions (one block per metric, bar chart each) ────────────────
    ws = wb.create_sheet("Distributions")
    blocks = [("v_required", "Vehicles required", INT), ("backlog", "Backlog (deliveries)", INT),
              ("total_cost", "Variable cost per day", EUR), ("maint_cost", "Maintenance cost per day", EUR),
              ("fuel_l", "Fuel per day (L)", INT)]
    col = 1
    for key, title, fmt in blocks:
        h = d["hist"][key]
        total = sum(h["counts"]) or 1
        ws.cell(row=1, column=col, value=title).font = H2_FONT
        _header(ws, 2, ["From", "To", "Days", "Share"], col)
        for i, (a, b, c) in enumerate(zip(h["edges"], h["edges"][1:], h["counts"]), 3):
            ws.cell(row=i, column=col, value=a).number_format = fmt
            ws.cell(row=i, column=col + 1, value=b).number_format = fmt
            ws.cell(row=i, column=col + 2, value=c)
            ws.cell(row=i, column=col + 3, value=c / total).number_format = PCT
        chart = BarChart()
        chart.title, chart.y_axis.title, chart.legend = title, "share of days", None
        chart.gapWidth = 20
        n = len(h["counts"])
        chart.add_data(Reference(ws, min_col=col + 3, min_row=2, max_row=2 + n), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=col, min_row=3, max_row=2 + n))
        chart.height, chart.width = 7, 14
        ws.add_chart(chart, f"{get_column_letter(col)}{n + 5}")
        for j in range(4):
            ws.column_dimensions[get_column_letter(col + j)].width = 12
        col += 5

    # ── Drivers (sensitivity) ───────────────────────────────────────────────
    ws = wb.create_sheet("Drivers")
    _header(ws, 1, ["Input", "ρ with late deliveries", "ρ with daily cost"])
    for i, (name, v) in enumerate(d["sensitivity"].items(), 2):
        ws.cell(row=i, column=1, value=name)
        ws.cell(row=i, column=2, value=v["shortfall"]).number_format = "0.00"
        ws.cell(row=i, column=3, value=v["cost"]).number_format = "0.00"
    n = len(d["sensitivity"])
    chart = BarChart()
    chart.type, chart.title = "bar", "Spearman rank correlation"
    chart.add_data(Reference(ws, min_col=2, max_col=3, min_row=1, max_row=1 + n), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=1 + n))
    chart.height, chart.width = 9, 16
    ws.add_chart(chart, "E2")
    _widths(ws, [26, 22, 20])

    # ── Fleet sizing ────────────────────────────────────────────────────────
    ws = wb.create_sheet("Fleet sizing")
    sz = d["sizing"]
    _header(ws, 1, ["Available vehicles", "Driver pool 520", "Driver pool grows (1.1/vehicle)"])
    for i, (v, a, b) in enumerate(zip(sz["vehicles"], sz["drivers_fixed"], sz["drivers_scaled"]), 2):
        ws.cell(row=i, column=1, value=v)
        ws.cell(row=i, column=2, value=a).number_format = PCT
        ws.cell(row=i, column=3, value=b).number_format = PCT
    n = len(sz["vehicles"])
    chart = LineChart()
    chart.title, chart.y_axis.title, chart.x_axis.title = "Fully on-time days", "share", "available vehicles"
    chart.add_data(Reference(ws, min_col=2, max_col=3, min_row=1, max_row=1 + n), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=1 + n))
    chart.height, chart.width = 9, 16
    ws.add_chart(chart, "E2")
    ws.cell(row=n + 3, column=1, value=d["text"]["sizing"]).alignment = WRAP
    ws.merge_cells(start_row=n + 3, start_column=1, end_row=n + 3, end_column=3)
    ws.row_dimensions[n + 3].height = 80
    _widths(ws, [20, 18, 30])

    # ── Convergence ─────────────────────────────────────────────────────────
    ws = wb.create_sheet("Convergence")
    cv = d["convergence"]
    _header(ws, 1, ["Scenarios", "Share of on-time days", "95% CI lower", "95% CI upper"])
    for i, row_vals in enumerate(zip(cv["n"], cv["p"], cv["lo"], cv["hi"]), 2):
        ws.cell(row=i, column=1, value=row_vals[0])
        for j, v in enumerate(row_vals[1:], 2):
            ws.cell(row=i, column=j, value=v).number_format = PCT
    n = len(cv["n"])
    chart = LineChart()
    chart.title, chart.x_axis.title = "Estimate as scenarios grow", "scenarios"
    chart.add_data(Reference(ws, min_col=2, max_col=4, min_row=1, max_row=1 + n), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=1 + n))
    chart.height, chart.width = 9, 16
    ws.add_chart(chart, "F2")
    _widths(ws, [12, 22, 14, 14])

    # ── Scenarios (every simulated day) ─────────────────────────────────────
    ws = wb.create_sheet("Scenarios")
    _header(ws, 1, ["Scenario"] + [h for _, h, _ in SCENARIO_COLUMNS])
    df = report.scenarios
    cols = [df[c].to_numpy() for c, _, _ in SCENARIO_COLUMNS]
    for i in range(len(df)):
        ws.append([i + 1] + [float(col[i]) for col in cols])
    for j, (_, _, fmt) in enumerate(SCENARIO_COLUMNS, 2):
        letter = get_column_letter(j)
        for cell in ws[letter][1:]:
            cell.number_format = fmt
        ws.column_dimensions[letter].width = 16
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

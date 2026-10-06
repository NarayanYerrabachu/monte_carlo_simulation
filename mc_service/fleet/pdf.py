"""PDF export of the fleet report (A4, reportlab)."""
from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from mc_service.branding import brand_page
from mc_service.fleet import charts
from mc_service.fleet.model import LIMITATIONS, NEXT_STEPS, FleetReport

# DejaVu ships with matplotlib and covers €, ρ, ≥, ≈ (the built-in PDF fonts do not)
_FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
pdfmetrics.registerFont(TTFont("DejaVu", str(_FONT_DIR / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(_FONT_DIR / "DejaVuSans-Bold.ttf")))

INK = colors.HexColor("#17201c")
MUTED = colors.HexColor("#737d78")
ACCENT = colors.HexColor("#1c5cab")
LINE = colors.HexColor("#dfe3e0")
PANEL = colors.HexColor("#f5f6f4")

S = {
    "title": ParagraphStyle("title", fontName="DejaVu-Bold", fontSize=20, leading=24, textColor=INK),
    "lede": ParagraphStyle("lede", fontName="DejaVu", fontSize=10.5, leading=15, textColor=MUTED),
    "eyebrow": ParagraphStyle("eyebrow", fontName="DejaVu-Bold", fontSize=7.5, leading=10, textColor=ACCENT,
                              spaceBefore=12),
    "h2": ParagraphStyle("h2", fontName="DejaVu-Bold", fontSize=13.5, leading=17, textColor=INK, spaceAfter=4),
    "h3": ParagraphStyle("h3", fontName="DejaVu-Bold", fontSize=10, leading=13, textColor=INK, spaceBefore=6),
    "body": ParagraphStyle("body", fontName="DejaVu", fontSize=9, leading=13, textColor=INK, alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName="DejaVu", fontSize=7.5, leading=10, textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName="DejaVu", fontSize=8, leading=10.5, textColor=INK),
    "big": ParagraphStyle("big", fontName="DejaVu-Bold", fontSize=15, leading=18, textColor=INK),
}


def _img(png: bytes, width_cm: float = 17.0) -> Image:
    img = Image(io.BytesIO(png))
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth, img.drawHeight = width_cm * cm, width_cm * cm * ratio
    return img


def _section(eyebrow: str, title: str, text: str | None = None) -> list:
    out = [Paragraph(eyebrow.upper(), S["eyebrow"]), Paragraph(title, S["h2"])]
    if text:
        out.append(Paragraph(text, S["body"]))
    out.append(Spacer(1, 6))
    return out


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("DejaVu", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(2 * cm, 1.2 * cm, "Monte Carlo Simulation in Fleet Management")
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(report: FleetReport) -> bytes:
    d = report.data
    png = charts.build(d)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=2 * cm, title="Monte Carlo Simulation in Fleet Management",
                            author="CortXplorer Monte Carlo Service")
    story: list = [
        Paragraph("FLEET MANAGEMENT · RISK ANALYSIS", S["eyebrow"]),
        Paragraph("Monte Carlo Simulation in Fleet Management", S["title"]),
        Spacer(1, 6),
        Paragraph("How likely is an operating day without delays, how many vehicles does the fleet really need, "
                  "and which costs must the budget cover? Instead of a single point estimate, this report runs "
                  f"through {d['n']:,} possible operating days.", S["lede"]),
        Spacer(1, 4),
        Paragraph(f"{d['n']:,} simulated operating days · seed {d['seed']} (reproducible) · "
                  f"generated {datetime.now(UTC):%d %b %Y}", S["small"]),
    ]

    # key results: 3 × 2 tiles
    tiles = [[Paragraph(f["value"], S["big"]), Paragraph(f"<b>{f['label']}</b><br/>{f['sub']}", S["cell"])]
             for f in d["findings"]]
    grid = [[_tile(tiles[i]), _tile(tiles[i + 1]), _tile(tiles[i + 2])] for i in (0, 3)]
    t = Table(grid, colWidths=[5.67 * cm] * 3)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += _section("Summary", "Key results") + [t]

    rows = [["Input", "Distribution", "Range", "Source"]] + [
        [Paragraph(r["input"], S["cell"]), Paragraph(r["distribution"], S["cell"]),
         Paragraph(r["range"], S["cell"]), Paragraph(r["source"], S["cell"])] for r in d["inputs"]]
    inputs = Table(rows, colWidths=[5.0 * cm, 5.0 * cm, 3.6 * cm, 3.4 * cm], repeatRows=1)
    inputs.setStyle(_table_style())
    story += _section("Model", "Uncertain inputs",
                      "Every input is drawn at random from its range in each scenario. <b>Brief</b> values come from "
                      "the fleet-management brief; <b>Assumption</b> values are placeholders to be replaced with "
                      "telematics, ERP or workshop data.") + [inputs]

    story += [KeepTogether(_section("Result 1", "On-time delivery and vehicle requirement", d["text"]["service"])
                           + [Paragraph("Share of simulated operating days by on-time delivery level", S["small"]),
                              _img(png["service"])]),
              Spacer(1, 4), _img(png["vehicles"])]
    k = d["kpi"]
    comp = Table([["Maintenance", "Fuel", "Overtime"],
                  [f"€{k['maint_cost_mean']:,.0f}", f"€{k['fuel_cost_mean']:,.0f}", f"€{k['overtime_cost_mean']:,.0f}"]],
                 colWidths=[5.67 * cm] * 3)
    comp.setStyle(_table_style())
    story += [KeepTogether(_section("Result 2", "Variable daily cost", d["text"]["cost"]) + [comp, Spacer(1, 6),
                                                                                          _img(png["cost"])]),
              _img(png["breakdowns_fuel"])]
    story += [KeepTogether(_section("Result 3", "What drives the risk",
                                    "Rank correlation (Spearman ρ) of each input with the share of late deliveries "
                                    "and with daily cost. Values near 0 mean this uncertainty hardly affects the "
                                    "outcome.") + [_img(png["sensitivity"])])]
    story += [KeepTogether(_section("What-if", "Fleet size and driver capacity", d["text"]["sizing"])
                           + [_img(png["sizing"])])]
    story += [KeepTogether(_section("Quality", "Are the scenarios enough?", d["text"]["convergence"])
                           + [_img(png["convergence"])])]
    story += _section("Decisions", "Recommendations", "Ordered by their effect on on-time delivery and cost.")
    for i, rec in enumerate(d["recommendations"], 1):
        story.append(KeepTogether([Paragraph(f"{i}. {rec['title']}", S["h3"]), Paragraph(rec["text"], S["body"])]))
    story += _section("Context", "Limitations and next steps")
    story += [Paragraph("Model limitations", S["h3"])] + [Paragraph(f"• {t}", S["body"]) for t in LIMITATIONS]
    story += [Paragraph("Next steps", S["h3"])] + [Paragraph(f"• {t}", S["body"]) for t in NEXT_STEPS]
    story += [Spacer(1, 10), Paragraph(
        "Monte Carlo method: each scenario redraws every uncertain input. Probabilities are shares of scenarios; "
        "percentiles refer to the distribution across all scenarios. Generated by the CortXplorer Monte Carlo "
        "service (mc_service/fleet).", S["small"])]

    doc.build(story, onFirstPage=brand_page(_footer), onLaterPages=brand_page(_footer))
    return buf.getvalue()


def _tile(cells: list) -> Table:
    t = Table([[cells[0]], [cells[1]]], colWidths=[5.5 * cm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PANEL), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (0, 0), 8),
                           ("BOTTOMPADDING", (0, -1), (-1, -1), 8)]))
    return t


def _table_style() -> TableStyle:
    return TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"), ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("FONTNAME", (0, 1), (-1, -1), "DejaVu"), ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ])

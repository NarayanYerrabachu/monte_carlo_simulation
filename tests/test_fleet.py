import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from mc_service import main
from mc_service.fleet.model import build_report, simulate

N = 2_000


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def test_report_is_deterministic_per_seed():
    a, b = build_report(N, 1).data, build_report.__wrapped__(N, 1).data   # bypass the cache
    assert a["kpi"] == b["kpi"] and a["text"] == b["text"]
    assert build_report(N, 2).data["kpi"] != a["kpi"]


def test_kpis_are_consistent():
    d = build_report(N, 1).data
    k = d["kpi"]
    assert 0 < k["p_day_on_time"] < 1
    assert k["p_day_on_time_ci"][0] < k["p_day_on_time"] < k["p_day_on_time_ci"][1]
    assert k["service_mean"] >= k["p_day_on_time"]            # a partly late day still delivers most
    assert k["v_required_p50"] <= k["v_required_p95"]
    assert k["total_cost_mean"] == pytest.approx(k["fuel_cost_mean"] + k["maint_cost_mean"] + k["overtime_cost_mean"])
    assert sum(d["service_bands"].values()) == pytest.approx(1)
    assert d["service_bands"]["100%"] == pytest.approx(k["p_day_on_time"])
    assert sum(d["breakdowns_pmf"]) == N
    assert len(d["recommendations"]) == 5 and len(d["findings"]) == 6


def test_more_drivers_never_hurt_in_sizing():
    sz = build_report(N, 1).data["sizing"]
    assert all(s >= f - 0.03 for f, s in zip(sz["drivers_fixed"], sz["drivers_scaled"]))


def test_simulate_backlog_zero_with_huge_fleet():
    import numpy as np
    df = simulate(np.random.default_rng(0), 500, vehicles=2_000, driver_pool=2_500)
    assert (df["backlog"] == 0).all()


def test_report_json_endpoint(client):
    r = client.get(f"/v1/reports/fleet?n={N}&seed=1")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["n"] == N and d["seed"] == 1 and d["kpi"] == build_report(N, 1).data["kpi"]


def test_report_rejects_bad_params(client):
    r = client.get("/v1/reports/fleet?n=10")
    assert r.status_code == 422 and r.json()["status"] == "error"


def test_pdf_download(client):
    r = client.get(f"/v1/reports/fleet.pdf?n={N}&seed=1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert f'filename="fleet-monte-carlo-{N}-seed1.pdf"' in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF") and len(r.content) > 50_000


def test_excel_download(client):
    r = client.get(f"/v1/reports/fleet.xlsx?n={N}&seed=1")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Summary", "Inputs", "Distributions", "Drivers", "Fleet sizing", "Convergence", "Scenarios"]
    assert wb["Scenarios"].max_row == N + 1
    k = build_report(N, 1).data["kpi"]
    assert wb["Summary"]["B5"].value == pytest.approx(k["p_day_on_time"])
    assert wb["Drivers"]._charts and wb["Fleet sizing"]._charts


def test_report_page_and_viewer_link(client):
    page = client.get("/report")
    assert page.status_code == 200 and "Download PDF" in page.text and "Download Excel" in page.text
    assert 'href="/report"' in client.get("/").text

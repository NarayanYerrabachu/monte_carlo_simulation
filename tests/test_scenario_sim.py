import threading

import numpy as np
import pytest

from mc_service.contract import ScenarioInput, SimSettings
from mc_service.live import LiveFeed
from mc_service.serialize import to_jsonable
from mc_service.simulations.base import RunContext
from mc_service.simulations.scenario import run


def _batches(n_periods=12, per_period=20, seed=0, bad_period=None):
    """Pharma-like table: one row per batch, a production week as the period."""
    rng = np.random.default_rng(seed)
    rows = {"record_id": [], "period": [], "regime": [], "anomaly_score": []}
    yield_pct, deviation, cost = [], [], []
    for p in range(n_periods):
        for b in range(per_period):
            bad = p == bad_period
            rows["record_id"].append(f"B{p}-{b}")
            rows["period"].append(f"2026-W{p + 1:02d}")
            rows["regime"].append(1 if bad else 0)
            rows["anomaly_score"].append(0.8 if bad else 0.1)
            yield_pct.append(70.0 if bad else float(rng.normal(92, 1)))
            deviation.append(1 if bad else 0)
            cost.append(1500.0 if bad else 1000.0)
    rows["metrics"] = [
        {"key": "yield_pct", "label": "Yield", "values": yield_pct, "unit": "%"},
        {"key": "deviation", "label": "Deviations", "values": deviation, "agg": "sum"},
        {"key": "cost", "label": "Cost", "values": cost, "agg": "sum", "unit": "€"},
    ]
    return rows


def _run(rows, n_sims=300, live=None, **kw):
    ctx = RunContext(settings=SimSettings(n_sims=n_sims, seed=7), n_jobs=1, cancel=threading.Event(),
                     progress=lambda d, t: None, live=live or LiveFeed())
    return to_jsonable(run(ScenarioInput(**rows, **kw), ctx))


def test_metrics_are_aggregated_per_period():
    res = _run(_batches(), title="Pharma batches", period_label="week", record_label="batches")
    k, cfg = res["summary"]["kpi"], res["config"]
    assert cfg["n_periods"] == 12 and cfg["period_size"] == 20 and "week-block" in cfg["method"]
    assert k["yield_pct_mean"] == pytest.approx(92, abs=0.5)            # mean metric
    assert k["cost_mean"] == pytest.approx(20 * 1000.0)                 # sum metric: 20 batches × €1000
    assert k["deviation_mean"] == 0 and k["high_mean"] == 0


def test_bad_period_shows_up_in_the_tail_and_its_regime():
    res = _run(_batches(bad_period=4), n_sims=600)
    k = res["summary"]["kpi"]
    assert k["p_any_high"] == pytest.approx(1 / 12, abs=0.04)           # 1 of 12 weeks is bad
    assert k["cost_p95"] == pytest.approx(20 * 1500.0)                  # the bad week is the cost tail
    assert k["cost_p_over"] is not None                                 # alert = P90 of the historical weeks
    regimes = {r["regime"]: r for r in res["results"]}
    assert regimes[1]["high_risk_share"] == 1.0 and regimes[0]["high_risk_share"] == 0.0
    assert regimes[1]["tail_lift"]["cost"] > 5                          # the bad regime drives the high-cost weeks
    assert regimes[1]["metrics"]["yield_pct"] == pytest.approx(70.0)


def test_without_periods_it_samples_all_records_and_has_no_default_alert():
    rows = _batches()
    rows.pop("period")
    res = _run(rows)
    assert res["config"]["n_periods"] is None and res["config"]["period_size"] == 100
    assert "no usable periods" in res["config"]["method"]
    assert res["summary"]["kpi"]["cost_alert"] is None and res["summary"]["kpi"]["cost_p_over"] is None


def test_alert_and_period_size_overrides():
    rows = _batches()
    rows["metrics"][2]["alert"] = 9000.0
    k = _run(rows, period_size=10)["summary"]["kpi"]
    assert k["cost_mean"] == pytest.approx(10 * 1000.0) and k["cost_p_over"] == 1.0


def test_dashboard_describes_tiles_charts_and_live_feed():
    live = LiveFeed()
    res = _run(_batches(bad_period=2), live=live, title="Pharma batches", period_label="week")
    dash = res["summary"]["dashboard"]
    assert dash["unit"] == "weeks" and [c["key"] for c in dash["charts"]] == ["yield_pct", "deviation", "cost", "high"]
    assert dash["tiles"][-1]["key"] == "high_share_mean" and len(dash["tiles"]) == 4
    snap = live.snapshot()
    assert snap["observed"]["dashboard"] == dash and snap["observed"]["mode"] == "none"
    assert set(snap["series"]) == {"m_yield_pct", "m_deviation", "m_cost", "high"}
    assert len(snap["series"]["m_cost"]) == snap["n_null"] == 300
    last = snap["checkpoints"][-1]
    assert last["n"] == 300 and all(t["key"] in last for t in dash["tiles"])
    assert snap["running"]["n"] == 300 and snap["running"]["share_at_least"] is None
    assert snap["frames"] and snap["frames"][-1]["day"].startswith("2026-W")


def test_same_seed_same_result_and_missing_values():
    rows = _batches()
    rows["metrics"][0]["values"][0] = None
    a, b = _run(rows), _run(rows)
    assert a["summary"]["kpi"] == b["summary"]["kpi"]
    assert np.isfinite(a["summary"]["kpi"]["yield_pct_mean"])


def test_mapper_groups_get_profile_and_frames_mark_them():
    rows = _batches(bad_period=1)
    rows["mapper"] = {"nodes": [{"id": 0, "x": 0, "y": 0, "z": 0, "members": list(range(20)), "label": "Normal"},
                                {"id": 1, "x": 1, "y": 0, "z": 1, "members": list(range(20, 40)), "label": "Bad week"}],
                      "edges": [[0, 1]]}
    live = LiveFeed()
    _run(rows, live=live)
    g = live.snapshot()["observed"]["graph"]
    bad = next(nd for nd in g["nodes"] if nd["id"] == 1)
    assert bad["risk"] == pytest.approx(0.8) and bad["profile"] == "high Deviations · high Cost"
    assert g["view"]["color_key"] == "risk" and g["edges"] == [[0, 1]]
    assert any(f["nodes"] for f in live.frames)


def test_backtest_holds_on_stable_data_and_fails_on_a_shift():
    """Simulated from the first 80 % of the periods; the last 20 % must fall in the 5–95 % band."""
    bt = _run(_batches(n_periods=30), n_sims=2000)["summary"]["backtest"]
    assert bt["available"] and (bt["train_periods"], bt["test_periods"]) == (24, 6)
    assert bt["train_until"] == "2026-W24" and bt["test_from"] == "2026-W25"
    assert bt["verdict"] == "holds" and all(r["coverage"] >= 0.8 for r in bt["metrics"])

    rows = _batches(n_periods=30)
    cost = rows["metrics"][2]["values"]
    for i, period in enumerate(rows["period"]):
        if period >= "2026-W25":                      # the hold-out weeks cost 40 % more than anything before
            cost[i] *= 1.4
    by_key = {r["key"]: r for r in _run(rows, n_sims=2000)["summary"]["backtest"]["metrics"]}
    assert by_key["cost"]["coverage"] == 0.0 and by_key["cost"]["verdict"] == "does not hold"
    assert by_key["yield_pct"]["verdict"] == "holds"  # the other metrics are unaffected


def test_backtest_needs_enough_periods():
    assert _run(_batches(n_periods=8))["summary"]["backtest"]["available"] is False
    rows = _batches()
    rows.pop("period")
    bt = _run(rows)["summary"]["backtest"]
    assert bt["available"] is False and "no usable periods" in bt["reason"]

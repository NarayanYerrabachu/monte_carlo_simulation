import threading

import numpy as np
import pytest

from mc_service.contract import FleetInput, SimSettings
from mc_service.live import LiveFeed
from mc_service.serialize import to_jsonable
from mc_service.simulations.base import RunContext
from mc_service.simulations.fleet import _delivery_time_hist, run


def _records(n_vehicles=20, n_days=10, seed=0, slow_day=None, p_break=0.0):
    rng = np.random.default_rng(seed)
    rows = {k: [] for k in ("record_id", "vehicle_id", "date", "regime", "anomaly_score", "deliveries_planned",
                            "deliveries_completed", "deliveries_on_time", "route_duration_h", "distance_km",
                            "fuel_l", "fuel_price_eur_l", "maintenance_cost_eur", "breakdown", "driver_available")}
    for d in range(n_days):
        for v in range(n_vehicles):
            planned = int(rng.integers(20, 30))
            slow = d == slow_day
            broke = int(rng.random() < p_break)
            completed = planned // 2 if broke else planned
            rows["record_id"].append(f"R{d}-{v}")
            rows["vehicle_id"].append(f"V{v}")
            rows["date"].append(f"2026-08-{d + 1:02d}")
            rows["regime"].append(1 if slow else 0)
            rows["anomaly_score"].append(0.1)
            rows["deliveries_planned"].append(planned)
            rows["deliveries_completed"].append(completed)
            rows["deliveries_on_time"].append(completed // 2 if slow else completed)
            rows["route_duration_h"].append(10.0 if slow else 6.0)
            rows["distance_km"].append(100.0)
            rows["fuel_l"].append(8.0)
            rows["fuel_price_eur_l"].append(1.7)
            rows["maintenance_cost_eur"].append(900.0 if broke else 0.0)
            rows["breakdown"].append(broke)
            rows["driver_available"].append(1)
    return rows


def _run(rows, n_sims=200, **kw):
    ctx = RunContext(settings=SimSettings(n_sims=n_sims, seed=7), n_jobs=1, cancel=threading.Event(),
                     progress=lambda d, t: None, live=LiveFeed())
    return to_jsonable(run(FleetInput(**rows, **kw), ctx))


def test_all_good_days_meet_the_sla():
    k = _run(_records())["summary"]["kpi"]
    assert k["p_meet_sla"] > 0.9 and k["fleet_size"] == 20
    assert k["p_delivery_within_target"] == pytest.approx(4 / 6, abs=0.01)   # 6 h routes, 4 h target
    assert k["fuel_l_mean"] == pytest.approx(20 * 8.0)
    assert 0 < k["fleet_availability_mean"] <= 1


def test_slow_day_shows_up_as_sla_risk_and_regime():
    res = _run(_records(slow_day=3))
    k = res["summary"]["kpi"]
    assert k["p_miss_sla"] == pytest.approx(0.1, abs=0.06)     # 1 of 10 days is slow
    regimes = {r["regime"]: r for r in res["results"]}
    assert regimes[1]["on_time_share"] < 0.6 < regimes[0]["on_time_share"]
    assert regimes[1]["late_deliveries_share"] > 0.5


def test_anomalies_are_excluded_from_the_pool():
    rows = _records()
    rows["anomaly_score"][0] = 0.95
    rows["fuel_l"][0] = 5000.0                                 # e.g. fuel-card misuse
    res = _run(rows)
    assert res["summary"]["anomalies"]["excluded"] == 1 and res["summary"]["anomalies"]["record_ids"] == ["R0-0"]
    assert res["summary"]["kpi"]["fuel_l_mean"] == pytest.approx(160.0)
    kept = _run(rows, exclude_anomalies_above=None)
    assert kept["summary"]["kpi"]["fuel_l_mean"] > 160.0


def test_breakdowns_cost_and_availability():
    k = _run(_records(p_break=0.1))["summary"]["kpi"]
    assert k["breakdowns_mean"] == pytest.approx(2.0, abs=0.4)          # 10% of 20 vehicles
    assert k["fleet_availability_mean"] == pytest.approx(0.9, abs=0.03)
    assert k["maint_cost_mean"] == pytest.approx(900 * k["breakdowns_mean"], rel=0.01)
    assert 0 <= k["p_breakdowns_over_alert"] <= 0.1                     # default alert = P90


def test_flagged_breakdowns_stay_in_the_pool():
    rows = _records(p_break=0.2)
    rows["anomaly_score"] = [0.9 if b else 0.1 for b in rows["breakdown"]]   # model flags every breakdown
    res = _run(rows)
    a = res["summary"]["anomalies"]
    assert a["excluded"] == 0 and a["kept_events"] == sum(rows["breakdown"])
    assert res["summary"]["kpi"]["breakdowns_mean"] == pytest.approx(4.0, abs=0.6)


def test_deterministic_and_fleet_size_what_if():
    a, b = _run(_records()), _run(_records())
    assert a["summary"]["kpi"] == b["summary"]["kpi"]
    base = _run(_records(slow_day=3))["summary"]["kpi"]
    big = _run(_records(slow_day=3), fleet_size=40)["summary"]["kpi"]
    # more vehicles serve the SAME demand: demand and vehicles required stay, on-time improves
    assert big["fleet_size"] == 40 and big["demand_mean"] == pytest.approx(base["demand_mean"], rel=0.02)
    assert big["vehicles_required_mean"] == pytest.approx(base["vehicles_required_mean"], rel=0.05)
    assert big["on_time_share_mean"] > base["on_time_share_mean"] and big["p_meet_sla"] >= base["p_meet_sla"]
    small = _run(_records(), fleet_size=10)["summary"]["kpi"]
    assert small["p_meet_sla"] < 0.05 and small["vehicles_required_mean"] == pytest.approx(20, abs=1)


def test_delivery_time_hist_shares():
    h = _delivery_time_hist(np.array([2.0, 8.0]), np.array([10.0, 10.0]), target_h=4.0)
    assert sum(h["share"]) == pytest.approx(1.0)
    assert h["p_within_target"] == pytest.approx((10 + 10 * 4 / 8) / 20)


def test_validation():
    rows = _records()
    rows["fuel_l"] = rows["fuel_l"][:-1]
    with pytest.raises(ValueError, match="fuel_l"):
        FleetInput(**rows)


def test_observed_inputs():
    res = _run(_records(p_break=0.1))
    inp = res["summary"]["inputs"]
    assert inp["records"] == 200 and inp["vehicles"] == 20 and inp["days"] == 10
    assert inp["fuel_l_per_100km"]["median"] == pytest.approx(8.0)
    assert inp["driver_availability"]["median"] == pytest.approx(1.0)
    assert inp["maintenance_cost_per_breakdown"]["median"] == pytest.approx(900.0)


def test_live_feed_for_the_viewer():
    feed = LiveFeed()
    ctx = RunContext(settings=SimSettings(n_sims=200, seed=7), n_jobs=1, cancel=threading.Event(),
                     progress=lambda d, t: None, live=feed)
    res = to_jsonable(run(FleetInput(**_records(slow_day=3)), ctx))
    snap = feed.snapshot()
    obs = snap["observed"]
    assert obs["mode"] == "share_at_least" and obs["stat"] == 0.95
    assert len(obs["points"]) == 200 and len(obs["groups"]) == 200 and set(obs["group_labels"]) == {"0", "1"}
    assert snap["n_null"] == 200
    assert snap["running"]["share_at_least"] == pytest.approx(res["summary"]["kpi"]["p_meet_sla"])
    assert snap["frame"]["index"] == 199 and 0 < len(snap["frame"]["highlight"]) <= 20
    # per-day series for the live charts, and server-computed running KPIs that end at the final result
    assert all(len(v) == 200 for v in snap["series"].values())
    last, k = snap["checkpoints"][-1], res["summary"]["kpi"]
    assert last["n"] == 200
    for key in ("p_meet_sla", "fleet_availability_mean", "maint_cost_mean", "vehicles_required_p95",
                "p_breakdowns_over_alert", "breakdown_alert"):
        assert last[key] == pytest.approx(k[key]), key
    assert feed.snapshot(since=150)["series"]["maint_cost"] == snap["series"]["maint_cost"][150:]


def test_mapper_graph_for_the_viewer():
    rows = _records(slow_day=3)
    n = len(rows["vehicle_id"])
    slow = [i for i, r in enumerate(rows["regime"]) if r == 1]
    fast = [i for i, r in enumerate(rows["regime"]) if r == 0]
    mapper = {"nodes": [{"id": 0, "x": 0, "y": 0, "z": -1, "size": len(fast), "members": fast,
                         "label": "Urban Above-Average", "short_label": "Urban Above-Avg"},
                        {"id": 7, "x": 1, "y": 1, "z": 1, "size": len(slow), "members": slow + [n + 5]}],
              "edges": [[0, 7], [0, 99]]}
    feed = LiveFeed()
    ctx = RunContext(settings=SimSettings(n_sims=100, seed=7), n_jobs=1, cancel=threading.Event(),
                     progress=lambda d, t: None, live=feed)
    run(FleetInput(**rows, mapper=mapper), ctx)
    snap = feed.snapshot()
    g = snap["observed"]["graph"]
    by_id = {nd["id"]: nd for nd in g["nodes"]}
    assert by_id[7]["size"] == len(slow)                     # out-of-range member dropped
    assert by_id[0]["short_label"] == "Urban Above-Avg" and by_id[7]["label"] is None
    assert "late deliveries" in by_id[7]["profile"] or "long days" in by_id[7]["profile"]   # the slow day
    assert by_id[7]["on_time"] < 0.6 < by_id[0]["on_time"]    # the slow day's records sit in node 7
    assert g["edges"] == [[0, 7]]                            # edge to an unknown node dropped
    assert len(snap["frames"]) >= 1 and snap["frames"][-1]["n"] == 100
    assert sum(c for _, c in snap["frames"][-1]["nodes"]) == 20   # 20 vehicles, one node each


def test_backtest_compares_the_last_days_with_a_simulation_from_the_first():
    bt = _run(_records(n_days=30, p_break=0.05), n_sims=2000)["summary"]["backtest"]
    assert bt["available"] and bt["unit"] == "days" and (bt["train_periods"], bt["test_periods"]) == (24, 6)
    assert {r["key"] for r in bt["metrics"]} == {"on_time_share", "breakdowns", "fuel_l", "maint_cost"}
    assert bt["verdict"] == "holds"
    slow = _run(_records(n_days=30, slow_day=28), n_sims=2000)["summary"]["backtest"]     # a late day only in the hold-out
    on_time = next(r for r in slow["metrics"] if r["key"] == "on_time_share")
    assert on_time["coverage"] < 1.0 and on_time["observed_min"] < on_time["sim_lo"]


def test_backtest_is_skipped_for_a_fleet_size_what_if():
    bt = _run(_records(n_days=30), fleet_size=35)["summary"]["backtest"]
    assert bt["available"] is False and "what-if" in bt["reason"]

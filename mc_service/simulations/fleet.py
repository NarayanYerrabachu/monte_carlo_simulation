"""Fleet operations Monte Carlo driven by real fleet records and their TDA/ML results.

Input: one record per vehicle and day (deliveries, route duration, distance,
fuel, maintenance, breakdown, driver availability), plus the TDA cluster
("regime") and ML anomaly score of every record, computed by CortXplorer.

Each simulated day:
  1. draws a historical day (keeps that day's weather, traffic and fuel price together),
  2. draws ``fleet_size`` vehicle-records from that day, with replacement,
  3. aggregates demand, on-time deliveries, deliveries within the target time,
     breakdowns, fuel, cost and the vehicles required to meet demand.

Records the ML model flags as anomalies (score ≥ ``exclude_anomalies_above``)
are left out of the sampling pool when the vehicle operated normally that day:
implausible values such as fuel-card misuse or odometer errors should not drive
the forecast. Breakdowns and absent drivers are real events, not data errors —
they stay in the pool even when the anomaly model flags them as unusual. The TDA regimes are reported
per regime, so the result shows *where* the risk comes from.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from mc_service.contract import FleetInput
from mc_service.engine import percentile_ci, simulate
from mc_service.simulations.base import RunContext

HIST_BINS = 30
# columns of the per-simulation result vector
COLS = ("demand", "on_time", "within_target", "breakdowns", "operating", "fuel_l", "fuel_cost",
        "maint_cost", "vehicles_required")


def _hist(x: np.ndarray, bins: int = HIST_BINS) -> dict[str, list]:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return {"edges": [], "counts": []}
    counts, edges = np.histogram(x, bins=bins)
    return {"edges": np.round(edges, 4).tolist(), "counts": counts.tolist()}


def _q(x: np.ndarray, p: float) -> float | None:
    x = x[np.isfinite(x)]
    return float(np.percentile(x, p)) if x.size else None


def _delivery_time_hist(duration: np.ndarray, completed: np.ndarray, target_h: float,
                        step: float = 0.25) -> dict[str, Any]:
    """Distribution of individual delivery times across the pool.

    A route's completed deliveries are spread evenly over its duration, so the
    share of a route's deliveries in [a, b) is the overlap of [a, b) with
    [0, duration) divided by the duration.
    """
    ok = (completed > 0) & (duration > 0)
    d, c = duration[ok], completed[ok]
    if d.size == 0:
        return {"edges": [], "share": [], "p_within_target": None}
    top = math.ceil(float(d.max()) / step) * step
    edges = np.arange(0, top + step, step)
    lo, hi = edges[:-1, None], edges[1:, None]
    overlap = np.clip(np.minimum(hi, d[None, :]) - lo, 0, None) / d[None, :]
    counts = (overlap * c[None, :]).sum(axis=1)
    share = counts / c.sum()
    within = float((np.minimum(target_h, d) / d * c).sum() / c.sum())
    return {"edges": np.round(edges, 3).tolist(), "share": np.round(share, 6).tolist(), "p_within_target": within}


def run(section: FleetInput, ctx: RunContext) -> dict:
    s = section
    n = len(s.vehicle_id)
    arr = lambda v: np.asarray(v, dtype=float)
    planned, completed, on_time = arr(s.deliveries_planned), arr(s.deliveries_completed), arr(s.deliveries_on_time)
    duration, fuel = arr(s.route_duration_h), arr(s.fuel_l)
    fuel_cost = fuel * arr(s.fuel_price_eur_l)
    maint = arr(s.maintenance_cost_eur)
    breakdown = arr(s.breakdown) > 0
    operating = (arr(s.driver_available) > 0) & ~breakdown
    regime = np.asarray(s.regime, dtype=int)
    dates = np.asarray(s.date)
    score = arr([np.nan if v is None else v for v in s.anomaly_score]) if s.anomaly_score else np.full(n, np.nan)

    # deliveries of each record completed within the target time (evenly spread over the route)
    with np.errstate(divide="ignore", invalid="ignore"):
        within = np.where(duration > 0, completed * np.minimum(1.0, s.delivery_target_h / duration), 0.0)

    excluded = np.zeros(n, dtype=bool)
    flagged_events = 0
    if s.exclude_anomalies_above is not None:
        flagged = np.nan_to_num(score, nan=-np.inf) >= s.exclude_anomalies_above
        excluded = flagged & operating                    # never drop breakdowns / absences
        flagged_events = int((flagged & ~operating).sum())
    pool = ~excluded
    days = sorted(set(dates[pool]))
    by_day = [np.flatnonzero(pool & (dates == d)) for d in days]
    by_day = [ix for ix in by_day if ix.size]
    fleet = s.fleet_size or len(set(s.vehicle_id))
    stats = np.column_stack([planned, on_time, within, breakdown, operating, fuel, fuel_cost, maint])

    def draw(rng: np.random.Generator) -> np.ndarray:
        ix = by_day[int(rng.integers(len(by_day)))]
        pick = ix[rng.integers(ix.size, size=fleet)]
        tot = stats[pick].sum(axis=0)
        per_vehicle_on_time = tot[1] / fleet
        v_req = math.ceil(tot[0] / per_vehicle_on_time) if per_vehicle_on_time > 0 else np.nan
        return np.append(tot, v_req)

    res = simulate(draw, ctx.sim_config(chunk_size=max(1, ctx.settings.n_sims // 50)))
    sim = {c: res.null[:, i] for i, c in enumerate(COLS)}
    share_on_time = sim["on_time"] / sim["demand"]
    share_within = sim["within_target"] / sim["demand"]
    meets = share_on_time >= s.sla_on_time
    availability = sim["operating"] / fleet
    alert = s.breakdown_alert if s.breakdown_alert is not None else math.ceil(float(np.percentile(sim["breakdowns"], 90)))
    ctx.live.add_null(share_on_time.tolist())

    k = np.arange(1, res.n_completed + 1)
    run_p = np.cumsum(meets) / k
    se = np.sqrt(run_p * (1 - run_p) / k)
    pts = np.unique(np.round(np.geomspace(min(50, res.n_completed), res.n_completed, 40)).astype(int)) if res.n_completed else []
    convergence = {"n": list(map(int, pts)), "p": [float(run_p[i - 1]) for i in pts],
                   "lo": [float(run_p[i - 1] - 1.96 * se[i - 1]) for i in pts],
                   "hi": [float(run_p[i - 1] + 1.96 * se[i - 1]) for i in pts]}

    # regimes (TDA clusters): observed profile of each, on the sampling pool
    regimes = []
    for r in sorted(set(regime[pool].tolist())):
        m = pool & (regime == r)
        pl = planned[m].sum()
        regimes.append({
            "regime": r, "label": (s.regime_labels or {}).get(str(r)) or ("Noise / outliers" if r == -1 else f"Cluster {r}"),
            "records": int(m.sum()), "share": float(m.sum() / pool.sum()),
            "on_time_share": float(on_time[m].sum() / pl) if pl else None,
            "within_target_share": float(within[m].sum() / pl) if pl else None,
            "breakdown_rate": float(breakdown[m].mean()), "availability": float(operating[m].mean()),
            "fuel_l_mean": float(fuel[m].mean()), "maint_cost_mean": float(maint[m].mean()),
            "late_deliveries_share": float((planned[m] - on_time[m]).sum() / max(1.0, (planned[pool] - on_time[pool]).sum())),
        })

    kpi = {
        "fleet_size": fleet,
        "p_meet_sla": float(meets.mean()), "p_miss_sla": float(1 - meets.mean()),
        "p_meet_sla_ci": list(percentile_ci(meets.astype(float), 0.95) or (None, None)),
        "on_time_share_mean": float(np.nanmean(share_on_time)),
        "p_delivery_within_target": float(np.nanmean(share_within)),
        "fleet_availability_mean": float(availability.mean()),
        "vehicles_required_mean": float(np.nanmean(sim["vehicles_required"])),
        "vehicles_required_p50": _q(sim["vehicles_required"], 50), "vehicles_required_p95": _q(sim["vehicles_required"], 95),
        "p_vehicles_required_gt_fleet": float(np.nanmean(sim["vehicles_required"] > fleet)),
        "demand_mean": float(sim["demand"].mean()),
        "fuel_l_mean": float(sim["fuel_l"].mean()), "fuel_l_p95": _q(sim["fuel_l"], 95),
        "fuel_cost_mean": float(sim["fuel_cost"].mean()), "fuel_cost_p95": _q(sim["fuel_cost"], 95),
        "breakdowns_mean": float(sim["breakdowns"].mean()), "breakdowns_p95": _q(sim["breakdowns"], 95),
        "breakdown_alert": alert, "p_breakdowns_over_alert": float((sim["breakdowns"] > alert).mean()),
        "maint_cost_mean": float(sim["maint_cost"].mean()), "maint_cost_p95": _q(sim["maint_cost"], 95),
        "total_cost_mean": float((sim["fuel_cost"] + sim["maint_cost"]).mean()),
        "total_cost_p95": _q(sim["fuel_cost"] + sim["maint_cost"], 95),
    }
    return {
        "test": "fleet",
        "n_completed": res.n_completed, "stopped_early": res.stopped_early, "elapsed_s": res.elapsed_s,
        "config": {"fleet_size": fleet, "delivery_target_h": s.delivery_target_h, "sla_on_time": s.sla_on_time,
                   "exclude_anomalies_above": s.exclude_anomalies_above, "n_records": n,
                   "n_pool": int(pool.sum()), "n_days": len(by_day), "n_sims": ctx.settings.n_sims,
                   "seed": ctx.settings.seed, "method": "day-block bootstrap of vehicle records"},
        "results": regimes,
        "summary": {
            "kpi": kpi,
            "outcome": {"on_time": kpi["on_time_share_mean"], "delayed": 1 - kpi["on_time_share_mean"]},
            "hist": {"vehicles_required": _hist(sim["vehicles_required"]), "maint_cost": _hist(sim["maint_cost"]),
                     "fuel_l": _hist(sim["fuel_l"]), "on_time_share": _hist(share_on_time),
                     "breakdowns": _hist(sim["breakdowns"], bins=int(max(1, sim["breakdowns"].max() - sim["breakdowns"].min() + 1)))},
            "delivery_time": _delivery_time_hist(duration[pool], completed[pool], s.delivery_target_h),
            "anomalies": {"threshold": s.exclude_anomalies_above, "excluded": int(excluded.sum()),
                          "kept_events": flagged_events,
                          "record_ids": [s.record_id[i] for i in np.flatnonzero(excluded)[:50]] if s.record_id else []},
            "convergence": convergence,
        },
    }

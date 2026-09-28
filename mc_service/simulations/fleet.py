"""Fleet operations Monte Carlo driven by real fleet records and their TDA/ML results.

Input: one record per vehicle and day (deliveries, route duration, distance,
fuel, maintenance, breakdown, driver availability), plus the TDA cluster
("regime") and ML anomaly score of every record, computed by CortXplorer.

Each simulated day:
  1. draws a historical day (keeps that day's weather, traffic and fuel price together);
     its demand is that day's deliveries per vehicle × the vehicles in the data, so the
     demand does not change when the fleet-size what-if changes,
  2. draws ``fleet_size`` vehicle-records from that day, with replacement,
  3. serves the demand with them: on-time deliveries = min(demand, Σ each vehicle's observed
     on-time deliveries). Using the observed on-time deliveries as a vehicle's capacity is a
     conservative lower bound — with a lighter load a vehicle could only do better,
  4. aggregates breakdowns, fuel, cost and the vehicles required to meet demand.

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
from mc_service.engine import percentile_ci, simulate, spawn_seeds
from mc_service.simulations.base import RunContext

HIST_BINS = 30
VIZ_MAX_POINTS = 3000         # records drawn in the live viewer's 3D cloud
VIZ_AXES = ("route_duration_h", "fuel_l", "deliveries_on_time")
VIZ_AXIS_TITLES = ("route duration (h)", "fuel (L)", "on-time deliveries")
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


def _spread(x: np.ndarray) -> dict[str, float] | None:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return None
    p5, med, p95 = np.percentile(x, [5, 50, 95])
    return {"p5": float(p5), "median": float(med), "p95": float(p95)}


# plain words for what makes a Mapper group different from the fleet: (column, word if higher, word if lower)
PROFILE_WORDS = [
    ("driver", None, "absent drivers"), ("breakdown", "breakdowns", None),
    ("on_time_rate", None, "late deliveries"), ("maintenance", "high maintenance cost", None),
    ("deliveries", "many stops", "few stops"), ("distance", "long routes", "short routes"),
    ("duration", "long days", "short days"), ("fuel", "high fuel", "low fuel"),
]


def _profile(cols: dict[str, np.ndarray], m: np.ndarray, mean: dict, std: dict, top: int = 2) -> str:
    """The one or two features where a group differs most from the fleet (|z| ≥ 0.6), in words."""
    scored = []
    for col, hi, lo in PROFILE_WORDS:
        if std[col] <= 0:
            continue
        z = (float(cols[col][m].mean()) - mean[col]) / std[col]
        word = hi if z > 0 else lo
        if word and abs(z) >= 0.6:
            scored.append((abs(z), word))
    scored.sort(reverse=True)
    return " · ".join(w for _, w in scored[:top]) or "typical fleet day"


def _mapper_graph(mapper: dict | None, n: int, on_time: np.ndarray, planned: np.ndarray,
                  breakdown: np.ndarray, operating: np.ndarray,
                  cols: dict[str, np.ndarray]) -> tuple[dict | None, dict[int, list[int]]]:
    """The sender's Mapper graph for the 3D view, with each node's record statistics and a
    plain-words profile, and the record → nodes index used to show where a simulated day's
    vehicles sit in the shape."""
    if not mapper or not mapper.get("nodes"):
        return None, {}
    mean = {c: float(v.mean()) for c, v in cols.items()}
    std = {c: float(v.std()) for c, v in cols.items()}
    nodes, record_nodes = [], {}
    for nd in mapper["nodes"]:
        m = np.array([r for r in nd.get("members", []) if 0 <= r < n], dtype=int)
        if m.size == 0:
            continue
        for r in m:
            record_nodes.setdefault(int(r), []).append(int(nd["id"]))
        pl = planned[m].sum()
        nodes.append({"id": int(nd["id"]), "x": nd["x"], "y": nd["y"], "z": nd["z"], "size": int(m.size),
                      "label": nd.get("label"), "short_label": nd.get("short_label"),
                      "profile": _profile(cols, m, mean, std),
                      "on_time": float(on_time[m].sum() / pl) if pl else None,
                      "breakdown_rate": float(breakdown[m].mean()), "availability": float(operating[m].mean())})
    ids = {nd["id"] for nd in nodes}
    edges = [[int(a), int(b)] for a, b in mapper.get("edges", []) if a in ids and b in ids]
    return {"nodes": nodes, "edges": edges}, record_nodes


def observed_inputs(dates: np.ndarray, planned: np.ndarray, breakdown: np.ndarray, driver: np.ndarray,
                    fuel: np.ndarray, distance: np.ndarray, duration: np.ndarray, price: np.ndarray,
                    maint: np.ndarray, vehicles: int) -> dict[str, Any]:
    """What the simulation samples from: per-day and per-record spreads (P5 / median / P95)."""
    days, inv = np.unique(dates, return_inverse=True)
    per_day = lambda v, how: (np.bincount(inv, weights=v) / (np.bincount(inv) if how == "mean" else 1))
    with np.errstate(divide="ignore", invalid="ignore"):
        per_100 = np.where(distance > 0, fuel / distance * 100, np.nan)
    return {
        "records": int(planned.size), "vehicles": vehicles, "days": int(days.size),
        "daily_demand": _spread(per_day(planned, "sum")),
        "daily_breakdowns": _spread(per_day(breakdown.astype(float), "sum")),
        "driver_availability": _spread(per_day(driver.astype(float), "mean")),
        "fuel_l_per_100km": _spread(per_100),
        "route_duration_h": _spread(duration[duration > 0]),
        "fuel_price_eur_l": _spread(per_day(price, "mean")),
        "maintenance_cost_per_breakdown": _spread(maint[breakdown]),
    }


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
    vehicles_in_data = len(set(s.vehicle_id))
    fleet = s.fleet_size or vehicles_in_data
    # demand of each historical day, per vehicle of the data (robust to excluded records)
    day_demand = [float(planned[ix].mean()) * vehicles_in_data for ix in by_day]
    stats = np.column_stack([on_time, within, breakdown, operating, fuel, fuel_cost, maint])
    if s.breakdown_alert is not None:
        alert = s.breakdown_alert
    else:                                   # P90 of historical daily breakdowns, scaled to the fleet
        hist_daily = np.array([breakdown[ix].sum() * vehicles_in_data / ix.size for ix in by_day])
        alert = math.ceil(float(np.percentile(hist_daily, 90)) * fleet / vehicles_in_data)

    def draw(rng: np.random.Generator) -> np.ndarray:
        d = int(rng.integers(len(by_day)))
        ix = by_day[d]
        demand = day_demand[d]
        tot = stats[rng.choice(ix, size=fleet)].sum(axis=0)
        served, within_t = min(demand, tot[0]), min(demand, tot[1])
        per_vehicle = tot[0] / fleet
        v_req = math.ceil(demand / per_vehicle) if per_vehicle > 0 else np.nan
        return np.array([demand, served, within_t, *tot[2:], v_req])

    # ── live viewer: records in 3D coloured by TDA regime; each simulated day highlights its vehicles
    viz = np.arange(n) if n <= VIZ_MAX_POINTS else np.sort(
        np.random.default_rng(ctx.settings.seed).choice(n, VIZ_MAX_POINTS, replace=False))
    viz_pos = {int(r): i for i, r in enumerate(viz)}
    axes = np.column_stack([arr(getattr(s, a)) for a in VIZ_AXES])[viz]
    labels = {str(r): (s.regime_labels or {}).get(str(r)) or ("Noise / outliers" if r == -1 else f"Cluster {r}")
              for r in sorted(set(regime.tolist()))}
    profile_cols = {"deliveries": planned, "on_time_rate": np.divide(on_time, planned, out=np.zeros(n), where=planned > 0),
                    "distance": arr(s.distance_km), "duration": duration, "fuel": fuel, "maintenance": maint,
                    "breakdown": breakdown.astype(float), "driver": arr(s.driver_available)}
    graph, record_nodes = _mapper_graph(s.mapper, n, on_time, planned, breakdown, operating, profile_cols)
    ctx.live.set_observed(
        stat=s.sla_on_time, alpha=ctx.settings.alpha, mode="share_at_least", graph=graph,
        points=np.round(axes, 3), groups=regime[viz], group_labels=labels, axis_titles=list(VIZ_AXIS_TITLES),
        statistic="on-time share of the simulated day", stat_label=f"SLA {s.sla_on_time:.0%}",
        band_label=f"{ctx.settings.alpha:.0%} worst days", running_label="P(meet SLA)",
        headline_label="Fleet size", headline=fleet)
    seeds = spawn_seeds(ctx.settings.seed, ctx.settings.n_sims)

    done: list[np.ndarray] = []                          # all draws so far, for the running KPIs

    def on_chunk(lo: int, draws: np.ndarray) -> None:
        demand_col, served_col = COLS.index("demand"), COLS.index("on_time")
        col = {c: draws[:, i] for i, c in enumerate(COLS)}
        # rounded to what the live charts need (KPIs below use full precision)
        ctx.live.add_series({
            "vehicles_required": col["vehicles_required"].tolist(),
            "maint_cost": np.round(col["maint_cost"]).tolist(),
            "fuel_l": np.round(col["fuel_l"]).tolist(), "breakdowns": col["breakdowns"].tolist(),
            "within_share": np.round(col["within_target"] / col["demand"], 4).tolist(),
        })
        ctx.live.add_null(np.round(draws[:, served_col] / draws[:, demand_col], 4).tolist())
        done.append(draws)
        a = np.concatenate(done)
        c = {k: a[:, i] for i, k in enumerate(COLS)}
        share = c["on_time"] / c["demand"]
        ctx.live.add_checkpoint({
            "n": len(a), "p_meet_sla": float((share >= s.sla_on_time).mean()),
            "on_time_share_mean": float(share.mean()),
            "p_delivery_within_target": float((c["within_target"] / c["demand"]).mean()),
            "fleet_availability_mean": float((c["operating"] / fleet).mean()),
            "fuel_cost_mean": float(c["fuel_cost"].mean()), "fuel_l_mean": float(c["fuel_l"].mean()),
            "breakdown_alert": alert, "p_breakdowns_over_alert": float((c["breakdowns"] > alert).mean()),
            "vehicles_required_mean": float(np.nanmean(c["vehicles_required"])),
            "vehicles_required_p95": float(np.nanpercentile(c["vehicles_required"], 95)),
            "maint_cost_mean": float(c["maint_cost"].mean()), "maint_cost_p95": float(np.percentile(c["maint_cost"], 95)),
        })
        i = lo + len(draws) - 1                         # rebuild the day simulation i drew
        rng = np.random.default_rng(seeds[i])
        d = int(rng.integers(len(by_day)))
        pick = rng.choice(by_day[d], size=fleet)
        shown = sorted({viz_pos[int(r)] for r in pick if int(r) in viz_pos})
        nodes: dict[int, int] = {}                    # Mapper node → vehicles of this day in it
        for r in pick:
            for k in record_nodes.get(int(r), ()):
                nodes[k] = nodes.get(k, 0) + 1
        ctx.live.set_frame(index=i, n=i + 1, day=str(dates[by_day[d][0]]), highlight=shown,
                           nodes=[[k, c] for k, c in sorted(nodes.items())],
                           on_time_share=float(draws[-1, served_col] / draws[-1, demand_col]))

    res = simulate(draw, ctx.sim_config(chunk_size=max(1, ctx.settings.n_sims // 100), on_chunk=on_chunk))
    sim = {c: res.null[:, i] for i, c in enumerate(COLS)}
    share_on_time = sim["on_time"] / sim["demand"]
    share_within = sim["within_target"] / sim["demand"]
    meets = share_on_time >= s.sla_on_time
    availability = sim["operating"] / fleet

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
            "inputs": observed_inputs(dates, planned, breakdown, arr(s.driver_available) > 0, fuel,
                                      arr(s.distance_km), duration, arr(s.fuel_price_eur_l), maint,
                                      len(set(s.vehicle_id))),
            "context": s.context,
        },
    }

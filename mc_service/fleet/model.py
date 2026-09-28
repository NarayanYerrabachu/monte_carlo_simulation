"""Fleet-management Monte Carlo: one operating day of a delivery fleet.

Input ranges follow the fleet-management brief (vehicles 450–500, breakdown
probability 1–5 %, driver availability 90–98 %, demand 8,000–12,000 deliveries,
traffic delay 5–60 min, fuel 7–10 L/100 km, maintenance 2–10 h, variable fuel
price). Everything else is an explicit assumption in ``ASSUMPTIONS`` — replace
it with telematics / ERP / workshop data for a real customer.

``build_report(n, seed)`` returns everything the HTML page, the PDF and the
Excel export show — numbers *and* the narrative text — so all three formats
say exactly the same thing. Deterministic for a given (n, seed).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ASSUMPTIONS: dict[str, Any] = {
    "driver_pool": 520,            # drivers employed
    "shift_min": 480,              # 8 h shift
    "stop_min": (15, 18, 24),      # min / mode / max minutes per delivery incl. driving to the next stop
    "km_per_stop": (2.5, 4.0),     # km driven per delivery
    "depot_km": 30,                # depot round trip per vehicle and day
    "fuel_price": (1.72, 0.10),    # EUR/L diesel, mean and sd
    "labour_eur_h": 95,            # workshop rate
    "parts_median_eur": 450,       # parts per breakdown (lognormal, sigma 0.6)
    "overtime_eur_h": 45,          # overtime rate for deliveries that miss the shift
}

# (input, distribution, range, source) — shown in every report format
INPUTS = [
    ("Available vehicles", "uniform, integer", "450–500", "Brief"),
    ("Breakdown probability per vehicle and day", "uniform; breakdowns binomial", "1–5%", "Brief"),
    ("Driver availability", "uniform, pool of 520 drivers", "90–98%", "Brief (pool: assumption)"),
    ("Daily demand", "uniform", "8,000–12,000 deliveries", "Brief"),
    ("Traffic delay per vehicle and day", "uniform", "5–60 min", "Brief"),
    ("Time per delivery, incl. driving to the next stop", "triangular (min / mode / max)", "15 / 18 / 24 min", "Assumption"),
    ("Fuel consumption", "uniform", "7–10 L/100 km", "Brief"),
    ("Distance driven", "2.5–4 km per delivery + 30 km depot run per vehicle", "≈ 46,000 km/day", "Assumption"),
    ("Fuel price (diesel)", "normal", "€1.72 ± €0.10 per L", "Brief: variable (values: assumption)"),
    ("Maintenance duration per breakdown", "uniform", "2–10 h", "Brief"),
    ("Workshop cost", "€95/h; parts lognormal (median €450)", "≈ €1,100 per breakdown", "Assumption"),
    ("Shift and overtime", "fixed", "8 h · €45/h", "Assumption"),
]

SENSITIVITY_INPUTS = {
    "Demand": "demand", "Time per delivery": "stop_min", "Traffic delay": "traffic_min",
    "Breakdown probability": "p_break", "Driver availability": "driver_rate",
    "Available vehicles": "v_avail", "Fuel price": "fuel_price",
}

SIZING_VEHICLES = list(range(440, 561, 10))
SIZING_SIMS = 4_000
DRIVERS_PER_VEHICLE = 1.1


def simulate(rng: np.random.Generator, n: int, vehicles: int | None = None,
             driver_pool: int | None = None) -> pd.DataFrame:
    """One row per simulated operating day."""
    a = ASSUMPTIONS
    v_avail = np.full(n, vehicles) if vehicles is not None else rng.integers(450, 501, n)
    p_break = rng.uniform(0.01, 0.05, n)
    breakdowns = rng.binomial(v_avail, p_break)
    driver_rate = rng.uniform(0.90, 0.98, n)
    drivers = rng.binomial(driver_pool or a["driver_pool"], driver_rate)
    v_use = np.minimum(v_avail - breakdowns, drivers)

    demand = rng.integers(8_000, 12_001, n)
    stop_min = rng.triangular(*a["stop_min"], n)
    traffic_min = rng.uniform(5, 60, n)                       # delay per vehicle and day
    cap_vehicle = (a["shift_min"] - traffic_min) / stop_min    # deliveries per vehicle in the shift
    capacity = v_use * cap_vehicle
    backlog = np.maximum(0, demand - capacity)

    km = demand * rng.uniform(*a["km_per_stop"], n) + v_use * a["depot_km"]
    fuel_l = km * rng.uniform(7, 10, n) / 100
    fuel_price = np.clip(rng.normal(*a["fuel_price"], n), 1.3, 2.3)

    kmax = int(breakdowns.max()) if n else 0
    mask = np.arange(kmax)[None, :] < breakdowns[:, None]
    hours = rng.uniform(2, 10, (n, kmax)) * mask
    parts = rng.lognormal(np.log(a["parts_median_eur"]), 0.6, (n, kmax)) * mask
    maint_cost = (hours * a["labour_eur_h"] + parts).sum(axis=1)

    overtime_h = backlog * stop_min / 60
    df = pd.DataFrame({
        "v_avail": v_avail, "p_break": p_break, "breakdowns": breakdowns, "driver_rate": driver_rate,
        "drivers": drivers, "v_use": v_use, "demand": demand, "stop_min": stop_min,
        "traffic_min": traffic_min, "capacity": capacity, "backlog": backlog,
        "service": np.minimum(1, capacity / demand), "v_required": np.ceil(demand / cap_vehicle),
        "km": km, "fuel_l": fuel_l, "fuel_price": fuel_price, "fuel_cost": fuel_l * fuel_price,
        "maint_cost": maint_cost, "downtime_h": hours.sum(axis=1),
        "overtime_h": overtime_h, "overtime_cost": overtime_h * a["overtime_eur_h"],
    })
    df["total_cost"] = df["fuel_cost"] + df["maint_cost"] + df["overtime_cost"]
    return df


def _hist(x: pd.Series, bins: int = 30) -> dict[str, list]:
    counts, edges = np.histogram(x, bins=bins)
    return {"edges": np.round(edges, 3).tolist(), "counts": counts.tolist()}


# ── text helpers (English, en-GB number style) ──────────────────────────────
def pct(v: float, d: int = 1) -> str:
    return f"{v * 100:.{d}f}%"


def eur(v: float) -> str:
    return f"€{v:,.0f}"


def num(v: float, d: int = 0) -> str:
    return f"{v:,.{d}f}"


@dataclass(frozen=True)
class FleetReport:
    data: dict[str, Any]          # everything the report shows (JSON-safe)
    scenarios: pd.DataFrame       # one row per simulated day (Excel export)


@lru_cache(maxsize=8)
def build_report(n: int = 10_000, seed: int = 42) -> FleetReport:
    r = simulate(np.random.default_rng(seed), n)
    on_time = (r["backlog"] <= 0).to_numpy()

    k = np.arange(1, n + 1)
    run_p = np.cumsum(on_time) / k
    se = np.sqrt(run_p * (1 - run_p) / k)
    pts = np.unique(np.round(np.geomspace(min(50, n), n, 40)).astype(int))
    convergence = {"n": pts.tolist(), "p": run_p[pts - 1].round(4).tolist(),
                   "lo": (run_p - 1.96 * se)[pts - 1].round(4).tolist(),
                   "hi": (run_p + 1.96 * se)[pts - 1].round(4).tolist()}

    shortfall = 1 - r["service"]
    sensitivity = {name: {"shortfall": round(float(spearmanr(r[col], shortfall).statistic), 3),
                          "cost": round(float(spearmanr(r[col], r["total_cost"]).statistic), 3)}
                   for name, col in SENSITIVITY_INPUTS.items()}

    sizing = {"vehicles": SIZING_VEHICLES, "drivers_fixed": [], "drivers_scaled": []}
    for v in SIZING_VEHICLES:
        for key, pool in (("drivers_fixed", None), ("drivers_scaled", round(v * DRIVERS_PER_VEHICLE))):
            s = simulate(np.random.default_rng(seed + v), SIZING_SIMS, vehicles=v, driver_pool=pool)
            sizing[key].append(round(float((s["backlog"] <= 0).mean()), 4))

    sv = r["service"]
    service_bands = {"100%": float((sv >= 1).mean()), "95-100%": float(((sv >= 0.95) & (sv < 1)).mean()),
                     "90-95%": float(((sv >= 0.90) & (sv < 0.95)).mean()), "<90%": float((sv < 0.90).mean())}
    has_b = r["breakdowns"] > 0
    cost_per_breakdown = float((r.loc[has_b, "maint_cost"] / r.loc[has_b, "breakdowns"]).mean())
    q = lambda col, p: float(np.percentile(r[col], p))

    kpi = {
        "p_day_on_time": float(on_time.mean()),
        "p_day_on_time_ci": [float(run_p[-1] - 1.96 * se[-1]), float(run_p[-1] + 1.96 * se[-1])],
        "service_mean": float(sv.mean()), "p_service_below_95": float((sv < 0.95).mean()),
        "backlog_mean": float(r["backlog"].mean()), "backlog_p95": q("backlog", 95),
        "v_required_p50": q("v_required", 50), "v_required_p95": q("v_required", 95),
        "v_required_mean": float(r["v_required"].mean()), "v_use_mean": float(r["v_use"].mean()),
        "p_required_gt_available": float((r["v_required"] > r["v_use"]).mean()),
        "breakdowns_mean": float(r["breakdowns"].mean()), "breakdowns_p95": q("breakdowns", 95),
        "p_breakdowns_ge_20": float((r["breakdowns"] >= 20).mean()),
        "downtime_h_mean": float(r["downtime_h"].mean()),
        "fuel_l_mean": float(r["fuel_l"].mean()), "fuel_l_p95": q("fuel_l", 95),
        "fuel_cost_mean": float(r["fuel_cost"].mean()), "fuel_cost_p95": q("fuel_cost", 95),
        "maint_cost_mean": float(r["maint_cost"].mean()), "maint_cost_p95": q("maint_cost", 95),
        "overtime_h_mean": float(r["overtime_h"].mean()), "overtime_cost_mean": float(r["overtime_cost"].mean()),
        "total_cost_mean": float(r["total_cost"].mean()), "total_cost_p5": q("total_cost", 5),
        "total_cost_p95": q("total_cost", 95), "km_mean": float(r["km"].mean()),
    }
    data = {
        "n": n, "seed": seed, "assumptions": ASSUMPTIONS,
        "inputs": [dict(zip(("input", "distribution", "range", "source"), row)) for row in INPUTS],
        "kpi": kpi,
        "hist": {c: _hist(r[c]) for c in ("v_required", "service", "backlog", "breakdowns", "fuel_l",
                                          "fuel_cost", "maint_cost", "total_cost")},
        "breakdowns_pmf": np.bincount(r["breakdowns"]).tolist(),
        "service_bands": service_bands, "cost_per_breakdown": cost_per_breakdown,
        "convergence": convergence, "sensitivity": sensitivity, "sizing": sizing,
    }
    data["findings"], data["text"], data["recommendations"] = _narrative(data)
    data["limitations"], data["next_steps"] = LIMITATIONS, NEXT_STEPS
    return FleetReport(data=data, scenarios=r)


def _narrative(d: dict[str, Any]) -> tuple[list[dict], dict[str, str], list[dict]]:
    """Key-result tiles, section texts and recommendations, all computed from the numbers."""
    k, S, sz = d["kpi"], d["sensitivity"], d["sizing"]
    plateau = max(sz["drivers_fixed"])
    first90 = next((v for v, p in zip(sz["vehicles"], sz["drivers_scaled"]) if p >= 0.9), None)
    first90_txt = num(first90) if first90 is not None else f"more than {num(sz['vehicles'][-1])}"
    ci = k["p_day_on_time_ci"]
    cv = d["convergence"]

    findings = [
        {"value": pct(k["p_day_on_time"]), "label": "Operating days without backlog",
         "sub": f"95% CI {pct(ci[0])} to {pct(ci[1])}"},
        {"value": pct(k["service_mean"]), "label": "Deliveries within the shift (mean)",
         "sub": f"below 95% on {pct(k['p_service_below_95'], 0)} of days"},
        {"value": num(k["v_required_p95"]), "label": "Vehicles required, 95th percentile",
         "sub": f"median {num(k['v_required_p50'])} · in service on average {num(k['v_use_mean'])}"},
        {"value": eur(k["total_cost_mean"]), "label": "Variable cost per day (mean)",
         "sub": f"90% of days between {eur(k['total_cost_p5'])} and {eur(k['total_cost_p95'])}"},
        {"value": num(k["breakdowns_mean"], 1), "label": "Vehicle breakdowns per day (mean)",
         "sub": f"P(≥ 20 breakdowns) = {pct(k['p_breakdowns_ge_20'])}"},
        {"value": f"{num(k['fuel_l_mean'])} L", "label": "Fuel per day (mean)",
         "sub": f"95th percentile {num(k['fuel_l_p95'])} L · mean {eur(k['fuel_cost_mean'])}"},
    ]
    text = {
        "service": (f"On average {pct(k['service_mean'])} of deliveries are completed within the shift. "
                    f"A fully on-time day, however, happens with only {pct(k['p_day_on_time'])} probability: "
                    f"on the remaining days a backlog is left, averaging {num(k['backlog_mean'])} deliveries "
                    f"across all days and exceeding {num(k['backlog_p95'])} on the worst 5% of days. "
                    f"On {pct(k['p_required_gt_available'], 0)} of days the fleet would need more vehicles "
                    f"than are in service."),
        "cost": (f"Variable cost averages {eur(k['total_cost_mean'])} per day, and on 90% of days lies between "
                 f"{eur(k['total_cost_p5'])} and {eur(k['total_cost_p95'])}. Maintenance is the largest and most "
                 f"volatile block (mean {eur(k['maint_cost_mean'])}, P95 {eur(k['maint_cost_p95'])}, "
                 f"≈ {eur(d['cost_per_breakdown'])} per breakdown). On average the workshop faces "
                 f"{num(k['downtime_h_mean'])} hours of work per day."),
        "sizing": (f"Each fleet size was re-simulated with {num(SIZING_SIMS)} scenarios. As long as the driver "
                   f"pool stays at {ASSUMPTIONS['driver_pool']}, the benefit of extra vehicles flattens out and "
                   f"stalls at around {pct(plateau, 0)} fully on-time days: drivers become the bottleneck. If the "
                   f"driver pool grows with the fleet, 90% is reachable from about {first90_txt} vehicles."),
        "convergence": (f"After {num(d['n'])} scenarios the share of fully on-time days is {pct(cv['p'][-1])}, "
                        f"with a 95% confidence interval of ±{(cv['hi'][-1] - cv['lo'][-1]) * 50:.1f} percentage "
                        f"points. More scenarios would not change the conclusions; the uncertainty lies in the "
                        f"assumptions, not in the number of scenarios."),
    }
    drivers90 = f"around {num(first90 * DRIVERS_PER_VEHICLE)}" if first90 is not None else "more"
    recommendations = [
        {"title": "Plan driver capacity before vehicles",
         "text": (f"With {ASSUMPTIONS['driver_pool']} drivers, a larger fleet adds almost no on-time delivery "
                  f"beyond about 500 vehicles (plateau at {pct(plateau, 0)}). Reaching 90% fully on-time days "
                  f"needs about {first90_txt} vehicles and {drivers90} drivers.")},
        {"title": "Use flexible capacity for peak days instead of a larger permanent fleet",
         "text": (f"A median day needs {num(k['v_required_p50'])} vehicles, but the 95th percentile is "
                  f"{num(k['v_required_p95'])}, above the entire fleet. Rental vehicles or subcontractors for "
                  f"peaks cost less than a fleet sized for the extreme case.")},
        {"title": "Cut the time per delivery",
         "text": (f"Demand (ρ {S['Demand']['shortfall']:.2f}) and time per delivery "
                  f"(ρ {S['Time per delivery']['shortfall']:.2f}) drive late deliveries; breakdowns barely do "
                  f"(ρ {S['Breakdown probability']['shortfall']:.2f}). Route planning and faster hand-overs "
                  f"improve on-time delivery more than extra maintenance.")},
        {"title": "Justify predictive maintenance on cost",
         "text": (f"Breakdown probability is the strongest cost driver (ρ {S['Breakdown probability']['cost']:.2f}). "
                  f"Each avoided breakdown saves about {eur(d['cost_per_breakdown'])} in workshop cost; the benefit "
                  f"shows up in the maintenance budget, not in punctuality.")},
        {"title": "Set the budget at the 95th percentile",
         "text": (f"Variable daily cost ranges from {eur(k['total_cost_p5'])} to {eur(k['total_cost_p95'])}. "
                  f"A budget based on the mean ({eur(k['total_cost_mean'])}) falls short on many days. The fuel "
                  f"price hardly affects day-to-day variation (ρ {S['Fuel price']['cost']:.2f}).")},
    ]
    return findings, text, recommendations


LIMITATIONS = [
    (
        "The ranges are brief values or assumptions, not measured customer data. The results show the method and "
        "the order of magnitude, not the figures of a specific fleet."
    ),
    (
        "Inputs are drawn independently. In reality, demand and traffic delay move together (weekday, weather), "
        "which would make the peaks worse."
    ),
    (
        "One scenario is one operating day. Multi-day effects such as a carried-over backlog or long workshop stays "
        "are not included."
    ),
    (
        "The example figures in the source graphic (e.g. 42,000 L of fuel per day) do not follow from the ranges "
        "given there. This report calculates every figure from the model: 7–10 L/100 km over ≈ 46,000 km gives "
        "about 3,900 L."
    ),
]
NEXT_STEPS = [
    (
        "Replace the ranges with real distributions: telematics (distances, dwell times), workshop system "
        "(breakdowns, duration, cost), dispatch and staffing data."
    ),
    "Model dependencies: weekday and weather scenarios, correlation between demand and traffic.",
    (
        "Set a planning target (e.g. 90% or 95% fully on-time days) and optimise fleet size, driver pool and "
        "flexible capacity against cost."
    ),
    "Let dispatch and controlling run their own what-if scenarios through this service.",
]

"""Backtest: does the simulation hold on periods it never saw?

The periods are split in time order: the first 80 % are the training periods,
the last 20 % the hold-out. A block-bootstrap simulation draws its periods
uniformly, so the draws of the main run that came from a training period *are*
a simulation from the training data alone — no second run is needed. For every
metric the hold-out periods' observed values are then compared with the 5–95 %
band of those draws: if the simulation describes the process, about 90 % of the
unseen periods fall inside it.

This checks the simulation's central assumption — that future periods look like
the past ones. It cannot be done without periods (no time order to hold out).
"""
from __future__ import annotations

from typing import Any

import numpy as np

HOLD_OUT = 0.2
MIN_PERIODS = 10               # fewer periods → hold-out too small to say anything
MIN_TRAIN_DRAWS = 200
BAND = (5, 95)                 # the simulated band the hold-out periods are checked against
HOLDS, PARTLY = 0.8, 0.6       # coverage at / above: "holds" / "partly holds"; below: "does not hold"


def verdict(coverage: float | None) -> str | None:
    if coverage is None:
        return None
    return "holds" if coverage >= HOLDS else "partly holds" if coverage >= PARTLY else "does not hold"


def unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def run(period_of_sim: np.ndarray, period_names: list[str], series: list[dict[str, Any]], unit: str) -> dict[str, Any]:
    """``period_of_sim``: for every simulation, the index (into ``period_names``, in time order)
    of the period it drew. ``series``: per metric ``{key, label, fmt, unit, sim, observed}`` with
    ``sim`` = the simulated value per simulation and ``observed`` = the real value per period."""
    n = len(period_names)
    if n < MIN_PERIODS:
        return unavailable(f"the data has {n} {unit}; a backtest needs at least {MIN_PERIODS}")
    n_test = max(2, round(n * HOLD_OUT))
    n_train = n - n_test
    from_train = period_of_sim < n_train
    if int(from_train.sum()) < MIN_TRAIN_DRAWS:
        return unavailable("too few simulations to backtest (run more scenarios)")
    rows = []
    for s in series:
        sim = np.asarray(s["sim"], dtype=float)[from_train]
        obs = np.asarray(s["observed"], dtype=float)[n_train:]
        sim, obs = sim[np.isfinite(sim)], obs[np.isfinite(obs)]
        if sim.size == 0 or obs.size == 0:
            continue
        lo, hi = np.percentile(sim, BAND)
        coverage = float(((obs >= lo) & (obs <= hi)).mean())
        rows.append({"key": s["key"], "label": s["label"], "fmt": s["fmt"], "unit": s.get("unit"),
                     "sim_mean": float(sim.mean()), "sim_lo": float(lo), "sim_hi": float(hi),
                     "observed_mean": float(obs.mean()), "observed_min": float(obs.min()), "observed_max": float(obs.max()),
                     "coverage": coverage, "verdict": verdict(coverage)})
    if not rows:
        return unavailable("no metric has values in both the training and the hold-out periods")
    overall = float(np.mean([r["coverage"] for r in rows]))
    return {"available": True, "unit": unit, "train_periods": n_train, "test_periods": n_test,
            "train_until": period_names[n_train - 1], "test_from": period_names[n_train], "test_until": period_names[-1],
            "band": list(BAND), "expected_coverage": (BAND[1] - BAND[0]) / 100, "train_draws": int(from_train.sum()),
            "metrics": rows, "coverage": overall, "verdict": verdict(overall)}

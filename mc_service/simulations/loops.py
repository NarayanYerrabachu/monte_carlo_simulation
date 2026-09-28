"""Loop (H1) significance: are the observed loops more persistent than loops in
structureless surrogate data of the same shape?

Observed: a seeded sample of ``sample_n`` rows of X → ripser (maxdim 1).
Null draw: surrogate of the full X (``gaussian`` = same mean + covariance,
``shuffle`` = columns permuted independently) → same sample size → ripser →
the **maximum** H1 persistence. Comparing every observed loop with the null
distribution of the maximum controls false loops across the whole diagram.
Observed and null use the same sample size because persistence depends on
sampling density.
"""
from __future__ import annotations

import numpy as np
from ripser import ripser

from mc_service.contract import LoopsInput
from mc_service.engine import p_values, simulate, spawn_seeds
from mc_service.nulls import column_shuffle, gaussian_surrogate
from mc_service.simulations.base import RunContext

MAX_LOOPS_REPORTED = 50
VIZ_POINTS = 600          # points per cloud sent to the live viewer
HIST_BINS = 30

SURROGATES = {"gaussian": gaussian_surrogate, "shuffle": column_shuffle}


def h1_diagram(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Finite H1 bars as (birth, death, persistence), sorted by persistence, longest first."""
    h1 = ripser(X, maxdim=1)["dgms"][1]
    h1 = h1[np.isfinite(h1[:, 1])]
    pers = h1[:, 1] - h1[:, 0]
    order = np.argsort(-pers, kind="stable")
    return h1[order, 0], h1[order, 1], pers[order]


def _sample(X: np.ndarray, m: int, rng: np.random.Generator) -> np.ndarray:
    return X[rng.choice(len(X), m, replace=False)]


def _viz(P: np.ndarray) -> np.ndarray:
    """First 3 dimensions (the demo sends PCA, so the leading components), zero-padded."""
    P = P[:VIZ_POINTS, :3]
    if P.shape[1] < 3:
        P = np.pad(P, ((0, 0), (0, 3 - P.shape[1])))
    return np.round(P, 4)


def run(section: LoopsInput, ctx: RunContext) -> dict:
    X = np.asarray(section.X, dtype=float)
    m = min(section.sample_n, len(X))
    surrogate = SURROGATES[section.null]
    seed, alpha = ctx.settings.seed, ctx.settings.alpha

    # Observed sample from its own stream, independent of the simulation seeds
    obs_rng = np.random.default_rng(np.random.SeedSequence([seed, 1]))
    births, deaths, pers = h1_diagram(_sample(X, m, obs_rng))
    top = float(pers[0]) if pers.size else 0.0
    ctx.live.set_observed(stat=top, alpha=alpha, points=_viz(X), statistic="longest H1 persistence",
                          heuristic_threshold=section.observed_noise_threshold)

    def draw(rng: np.random.Generator) -> float:
        S = _sample(surrogate(X, rng), m, rng)
        p = h1_diagram(S)[2]
        return float(p[0]) if p.size else 0.0

    seeds = spawn_seeds(seed, ctx.settings.n_sims)

    def on_chunk(lo: int, draws: np.ndarray) -> None:
        ctx.live.add_null(draws.reshape(-1).tolist())
        i = lo + len(draws) - 1                        # rebuild the surrogate simulation i used
        rng = np.random.default_rng(seeds[i])
        ctx.live.set_frame(index=i, points=_viz(_sample(surrogate(X, rng), m, rng)))

    res = simulate(draw, ctx.sim_config(chunk_size=ctx.live_chunk_size(), on_chunk=on_chunk))
    null = res.null.reshape(-1)

    shown = slice(0, MAX_LOOPS_REPORTED)
    # every loop against the same null of maxima → broadcast the null over loops
    p = p_values(pers[shown], null[:, None]) if pers.size else np.array([])
    results = [
        {"index": k, "birth": births[k], "death": deaths[k], "persistence": pers[k],
         "p_value": p[k], "significant": bool(p[k] <= alpha)}
        for k in range(len(p))
    ]
    has_null = null.size > 0
    counts, edges = np.histogram(null, bins=HIST_BINS) if has_null else (np.array([]), np.array([]))
    q = (lambda v: float(np.quantile(null, v))) if has_null else (lambda v: None)
    return {
        "test": "loops",
        "n_completed": res.n_completed,
        "stopped_early": res.stopped_early,
        "elapsed_s": res.elapsed_s,
        "config": {"null": section.null, "sample_n": m, "n_points": len(X), "dims": X.shape[1],
                   "n_sims": ctx.settings.n_sims, "seed": seed, "alpha": alpha},
        "results": results,
        "summary": {
            "noise_band": q(1 - alpha),
            "heuristic_threshold": section.observed_noise_threshold,
            "top_persistence": top,
            "n_loops_observed": int(pers.size),
            "n_significant": sum(r["significant"] for r in results),
            "null_max_quantiles": {"p50": q(0.5), "p90": q(0.9), "p95": q(0.95), "p99": q(0.99)},
            "null_hist": {"edges": edges, "counts": counts},
        },
    }

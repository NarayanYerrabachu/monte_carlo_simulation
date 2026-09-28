"""Monte Carlo engine: seeded simulation runs, empirical p-values, BH, CIs.

Determinism: simulation *i* always draws from
``default_rng(spawn_seeds(seed, n_sims)[i])`` — the result does not depend on
worker count or chunk size. Only a run cut short by the time budget can differ
(fewer completed simulations), and it says so (``stopped_early``). Because the
seeds are index-based, a caller can rebuild the exact random input of
simulation *i* afterwards (the live viewer does this to show a surrogate).
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from joblib import Parallel, delayed

# Default simulations per scheduling step: cancel / time-budget checks and the
# progress + ``on_chunk`` callbacks happen between chunks.
CHUNK_SIZE = 32


class Cancelled(Exception):
    """Raised inside a run when its job was cancelled."""


@dataclass
class SimConfig:
    n_sims: int
    seed: int
    n_jobs: int = 1
    max_seconds: float | None = None
    progress: Callable[[int, int], None] | None = None
    cancel: threading.Event | None = None
    chunk_size: int = CHUNK_SIZE
    # Called after each chunk with (index of its first simulation, its draws);
    # used to stream the null distribution to the live viewer.
    on_chunk: Callable[[int, np.ndarray], None] | None = None


@dataclass
class SimResult:
    null: np.ndarray        # shape (n_completed, *statistic shape)
    n_completed: int
    elapsed_s: float
    stopped_early: bool


def spawn_seeds(seed: int, n_sims: int) -> list[np.random.SeedSequence]:
    """The per-simulation seeds ``simulate`` uses (index i → simulation i)."""
    return np.random.SeedSequence(seed).spawn(n_sims)


def _draw(draw_null: Callable[[np.random.Generator], Any], seed: np.random.SeedSequence) -> np.ndarray:
    return np.asarray(draw_null(np.random.default_rng(seed)), dtype=float)


def simulate(draw_null: Callable[[np.random.Generator], Any], cfg: SimConfig) -> SimResult:
    """Run ``draw_null`` ``cfg.n_sims`` times, each with its own seeded generator.

    ``draw_null`` returns a scalar or an array (same shape every call). With
    ``n_jobs > 1`` it runs in loky worker processes, so it must be picklable
    (closures are fine — loky uses cloudpickle).
    """
    seeds = spawn_seeds(cfg.seed, cfg.n_sims)
    step = max(1, cfg.chunk_size)
    start = time.monotonic()
    draws: list[np.ndarray] = []
    stopped_early = False
    parallel = Parallel(n_jobs=cfg.n_jobs, backend="loky") if cfg.n_jobs != 1 else None

    for lo in range(0, cfg.n_sims, step):
        if cfg.cancel is not None and cfg.cancel.is_set():
            raise Cancelled()
        if cfg.max_seconds is not None and draws and time.monotonic() - start > cfg.max_seconds:
            stopped_early = True
            break
        chunk = seeds[lo:lo + step]
        if parallel is None:
            new = [_draw(draw_null, s) for s in chunk]
        else:
            new = parallel(delayed(_draw)(draw_null, s) for s in chunk)
        draws.extend(new)
        if cfg.on_chunk is not None:
            cfg.on_chunk(lo, np.stack(new))
        if cfg.progress is not None:
            cfg.progress(len(draws), cfg.n_sims)

    null = np.stack(draws) if draws else np.empty((0,))
    return SimResult(null=null, n_completed=len(draws),
                     elapsed_s=round(time.monotonic() - start, 3), stopped_early=stopped_early)


def p_values(observed: Any, null: np.ndarray, tail: str = "greater") -> np.ndarray:
    """Empirical p-values with the +1 correction: (1 + #{null ≥ obs}) / (1 + n).

    ``null`` has shape (n_sims, *observed.shape). NaN simulations are left out
    per statistic; a NaN observed value, or no finite simulation, gives NaN.
    ``tail``: "greater" (large = extreme), "less", or "two-sided"
    (min(1, 2·min(greater, less))).
    """
    obs = np.asarray(observed, dtype=float)
    null = np.asarray(null, dtype=float)
    if null.shape[0] == 0:
        return np.full(obs.shape, np.nan)
    finite = np.isfinite(null)
    n = finite.sum(axis=0)
    with np.errstate(invalid="ignore"):
        ge = ((null >= obs) & finite).sum(axis=0)
        le = ((null <= obs) & finite).sum(axis=0)
        p_ge = (1 + ge) / (1 + n)
        p_le = (1 + le) / (1 + n)
    if tail == "greater":
        p = p_ge
    elif tail == "less":
        p = p_le
    elif tail == "two-sided":
        p = np.minimum(1.0, 2 * np.minimum(p_ge, p_le))
    else:
        raise ValueError(f"Unknown tail {tail!r}")
    return np.where(np.isfinite(obs) & (n > 0), p, np.nan)


def p_value(observed: float | None, null: np.ndarray, tail: str = "greater") -> float | None:
    """Scalar form of ``p_values``; None when undefined."""
    if observed is None:
        return None
    p = float(p_values(observed, np.asarray(null, dtype=float).reshape(-1), tail))
    return None if np.isnan(p) else p


def bh_qvalues(p: Any) -> np.ndarray:
    """Benjamini–Hochberg q-values (monotone, ≤ 1). NaN entries stay NaN and do
    not count towards the number of tests."""
    p = np.asarray(p, dtype=float)
    q = np.full(p.shape, np.nan)
    ok = np.flatnonzero(np.isfinite(p))
    m = len(ok)
    if m == 0:
        return q
    order = ok[np.argsort(p[ok], kind="stable")]
    ranked = p[order] * m / np.arange(1, m + 1)
    q[order] = np.minimum(1.0, np.minimum.accumulate(ranked[::-1])[::-1])
    return q


def percentile_ci(samples: Any, level: float = 0.95) -> tuple[float, float] | None:
    """Percentile interval of the finite samples; None when there are none."""
    s = np.asarray(samples, dtype=float)
    s = s[np.isfinite(s)]
    if s.size == 0:
        return None
    tail = (1 - level) / 2 * 100
    lo, hi = np.percentile(s, [tail, 100 - tail])
    return float(lo), float(hi)

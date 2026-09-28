"""Live feed of a running test, polled by the viewer (``GET /v1/jobs/{id}/live``).

The job thread writes, request threads read. Writes replace whole values or
append to a list, and reads copy, so no lock is needed under the GIL. The
running statistics are computed here, server-side — the viewer only draws what
it receives. Two modes:

- ``p_value`` (loop test): p-value of the observed statistic against the null,
  and the (1 − α) noise band.
- ``share_at_least`` (fleet): share of simulated days whose value reaches the
  target ``stat`` (e.g. P(on-time share ≥ SLA)), and the α-quantile (bad days).
"""
from __future__ import annotations

from typing import Any

import numpy as np

from mc_service.engine import p_value
from mc_service.serialize import to_jsonable


class LiveFeed:
    def __init__(self) -> None:
        self.observed: dict[str, Any] | None = None   # static: observed points + marker values
        self.frame: dict[str, Any] | None = None      # latest surrogate the null was computed on
        self.frames: list[dict[str, Any]] = []        # every frame, for playback (small: node / point ids)
        self.null: list[float] = []                   # null statistic per completed simulation
        self.series: dict[str, list] = {}             # per-simulation values (fleet: one entry per metric)
        self.checkpoints: list[dict[str, Any]] = []   # running KPIs, computed server-side after each chunk
        self._stat: float | None = None               # observed statistic / target compared with the null
        self._alpha = 0.05
        self._mode = "p_value"

    def set_observed(self, stat: float | None, alpha: float, mode: str = "p_value", **payload: Any) -> None:
        self._stat, self._alpha, self._mode = stat, alpha, mode
        self.observed = to_jsonable({"stat": stat, "alpha": alpha, "mode": mode, **payload})

    def set_frame(self, **payload: Any) -> None:
        self.frame = to_jsonable(payload)
        self.frames.append(self.frame)

    def add_null(self, values: list[float]) -> None:
        self.null.extend(to_jsonable(values))

    def add_series(self, values: dict[str, list[float]]) -> None:
        """Per-simulation metrics, index-aligned with ``null``."""
        for key, vals in values.items():
            self.series.setdefault(key, []).extend(to_jsonable(vals))

    def add_checkpoint(self, kpis: dict[str, Any]) -> None:
        self.checkpoints.append(to_jsonable(kpis))

    def running(self) -> dict[str, Any]:
        """Running statistics so far (see the module docstring for the two modes)."""
        null = np.array([v for v in self.null if v is not None], dtype=float)
        if null.size == 0:
            return {"n": 0, "p_value": None, "noise_band": None, "share_at_least": None}
        if self._mode == "share_at_least":
            return {"n": int(null.size), "p_value": None, "share_at_least": float((null >= self._stat).mean()),
                    "noise_band": float(np.quantile(null, self._alpha))}
        return {"n": int(null.size), "p_value": p_value(self._stat, null), "share_at_least": None,
                "noise_band": float(np.quantile(null, 1 - self._alpha))}

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        """Null values from index ``since`` on; the observed payload only on the first poll."""
        since = max(0, since)
        null = self.null[:]
        return {
            "observed": self.observed if since == 0 else None,
            "frame": self.frame,
            "null_from": since,
            "null": null[since:],
            "n_null": len(null),
            "series": {k: v[since:len(null)] for k, v in self.series.items()},
            "checkpoints": self.checkpoints[:],
            "frames": self.frames[:],
            "running": self.running(),
        }

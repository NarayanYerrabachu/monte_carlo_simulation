"""Live feed of a running test, polled by the viewer (``GET /v1/jobs/{id}/live``).

The job thread writes, request threads read. Writes replace whole values or
append to a list, and reads copy, so no lock is needed under the GIL. The
running p-value and noise band are computed here, server-side — the viewer
only draws what it receives.
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
        self.null: list[float] = []                   # null statistic per completed simulation
        self._stat: float | None = None               # observed statistic compared with the null
        self._alpha = 0.05

    def set_observed(self, stat: float | None, alpha: float, **payload: Any) -> None:
        self._stat, self._alpha = stat, alpha
        self.observed = to_jsonable({"stat": stat, "alpha": alpha, **payload})

    def set_frame(self, **payload: Any) -> None:
        self.frame = to_jsonable(payload)

    def add_null(self, values: list[float]) -> None:
        self.null.extend(to_jsonable(values))

    def running(self) -> dict[str, Any]:
        """p-value of the observed statistic and the (1 − α) noise band so far."""
        null = np.array([v for v in self.null if v is not None], dtype=float)
        if null.size == 0:
            return {"n": 0, "p_value": None, "noise_band": None}
        return {"n": int(null.size), "p_value": p_value(self._stat, null),
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
            "running": self.running(),
        }

"""What every test runner gets: settings, worker count, cancel flag, progress and live feed."""
from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pydantic import BaseModel

from mc_service.contract import SimSettings
from mc_service.engine import CHUNK_SIZE, SimConfig
from mc_service.live import LiveFeed


@dataclass
class RunContext:
    settings: SimSettings
    n_jobs: int
    cancel: threading.Event
    progress: Callable[[int, int], None]
    live: LiveFeed = field(default_factory=LiveFeed)

    def sim_config(self, n_sims: int | None = None, chunk_size: int = CHUNK_SIZE,
                   on_chunk: Callable[[int, np.ndarray], None] | None = None) -> SimConfig:
        return SimConfig(
            n_sims=n_sims or self.settings.n_sims,
            seed=self.settings.seed,
            n_jobs=self.n_jobs,
            max_seconds=self.settings.max_seconds,
            progress=self.progress,
            cancel=self.cancel,
            chunk_size=chunk_size,
            on_chunk=on_chunk,
        )

    def live_chunk_size(self) -> int:
        """Small chunks so the viewer updates per simulation (one per worker when parallel)."""
        if self.n_jobs == 1:
            return 1
        workers = (os.cpu_count() or 1) if self.n_jobs < 0 else self.n_jobs
        return 2 * workers


Runner = Callable[[BaseModel, RunContext], dict[str, Any]]

"""Monte Carlo tests. Each module registers a runner in ``RUNNERS``.

A runner is ``run(section, ctx) -> dict`` with the ``TestResult`` fields;
``section`` is that test's validated input model. Tests without a runner are
reported per test as not implemented (the other tests of the job still run).
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from mc_service.contract import SimSettings
from mc_service.engine import SimConfig


@dataclass
class RunContext:
    settings: SimSettings
    n_jobs: int
    cancel: threading.Event
    progress: Callable[[int, int], None]

    def sim_config(self, n_sims: int | None = None) -> SimConfig:
        return SimConfig(
            n_sims=n_sims or self.settings.n_sims,
            seed=self.settings.seed,
            n_jobs=self.n_jobs,
            max_seconds=self.settings.max_seconds,
            progress=self.progress,
            cancel=self.cancel,
        )


Runner = Callable[[BaseModel, RunContext], dict[str, Any]]

RUNNERS: dict[str, Runner] = {}

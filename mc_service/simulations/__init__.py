"""Monte Carlo tests. ``RUNNERS`` maps a test name to its runner.

A runner is ``run(section, ctx) -> dict`` with the ``TestResult`` fields;
``section`` is that test's validated input model. Tests without a runner are
reported per test as not implemented (the other tests of the job still run).
"""
from __future__ import annotations

from mc_service.simulations import fleet, loops
from mc_service.simulations.base import RunContext, Runner

RUNNERS: dict[str, Runner] = {
    "loops": loops.run,
    "fleet": fleet.run,
}

__all__ = ["RUNNERS", "RunContext", "Runner"]

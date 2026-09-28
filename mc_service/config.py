"""Service configuration from environment variables (read once at startup).

Statistical settings (n_sims, seed, alpha, time budget) are *not* here: they
travel in each request (``SimSettings``) so the caller controls them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceConfig:
    workers: int          # MC_WORKERS: jobs running in parallel
    n_jobs: int           # MC_N_JOBS: processes per simulation run (1 = in-process)
    max_body_bytes: int   # MC_MAX_BODY_MB: request size limit (before and after gzip)
    job_ttl_s: float      # MC_JOB_TTL_S: finished jobs kept in memory

    @classmethod
    def from_env(cls) -> ServiceConfig:
        return cls(
            workers=max(1, int(os.getenv("MC_WORKERS", "2"))),
            n_jobs=int(os.getenv("MC_N_JOBS", "1")),
            max_body_bytes=int(float(os.getenv("MC_MAX_BODY_MB", "100")) * 1024 * 1024),
            job_ttl_s=float(os.getenv("MC_JOB_TTL_S", "3600")),
        )

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
    job_ttl_s: float      # MC_JOB_TTL_S: finished jobs kept (memory and disk), default 7 days
    job_dir: str | None   # MC_JOB_DIR: where finished jobs are saved so they survive restarts (unset: memory only)
    rerun_ttl_s: float    # MC_RERUN_TTL_S: how long a fleet job's records stay in memory for what-if re-runs
    transport: str        # MC_TRANSPORT: "http" (jobs arrive on /v1/jobs) or "kafka" (also consume mc.jobs.requested)
    kafka_servers: str    # KAFKA_BOOTSTRAP_SERVERS (kafka transport only)

    @classmethod
    def from_env(cls) -> ServiceConfig:
        return cls(
            workers=max(1, int(os.getenv("MC_WORKERS", "2"))),
            n_jobs=int(os.getenv("MC_N_JOBS", "1")),
            max_body_bytes=int(float(os.getenv("MC_MAX_BODY_MB", "100")) * 1024 * 1024),
            job_ttl_s=float(os.getenv("MC_JOB_TTL_S", str(7 * 24 * 3600))),
            job_dir=os.getenv("MC_JOB_DIR") or None,
            rerun_ttl_s=float(os.getenv("MC_RERUN_TTL_S", "3600")),
            transport="kafka" if os.getenv("MC_TRANSPORT", "http").strip().lower() == "kafka" else "http",
            kafka_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip(),
        )

"""In-memory job store: one thread pool, one job per request, tests run in sequence.

A job's tests run one after another; a failing test is recorded in
``errors`` and the others still run (partial results are useful). The input
data is dropped as soon as the job ends — only results stay, for
``MC_JOB_TTL_S`` seconds.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from mc_service.contract import SimulationRequest, SimulationResponse
from mc_service.engine import Cancelled
from mc_service.serialize import to_jsonable
from mc_service.simulations import RUNNERS, RunContext

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")


@dataclass
class Job:
    id: str
    dataset_id: str
    tests: list[str]
    request: SimulationRequest | None
    status: str = "queued"          # queued | running | done | failed | cancelled
    created: float = field(default_factory=time.time)
    finished: float | None = None
    progress: dict[str, dict[str, int]] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    cancel: threading.Event = field(default_factory=threading.Event)

    def status_view(self) -> dict[str, Any]:
        end = self.finished or time.time()
        return {
            "job_id": self.id,
            "dataset_id": self.dataset_id,
            "status": self.status,
            "tests": self.tests,
            "progress": self.progress,
            "errors": self.errors,
            "elapsed_s": round(end - self.created, 3),
        }

    def response(self) -> dict[str, Any]:
        return SimulationResponse(dataset_id=self.dataset_id, job_id=self.id,
                                  errors=self.errors, **self.results).model_dump()


class JobStore:
    def __init__(self, workers: int, n_jobs: int, ttl_s: float):
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mc-job")
        self._n_jobs = n_jobs
        self._ttl_s = ttl_s
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, request: SimulationRequest) -> Job:
        tests = request.requested_tests()
        job = Job(id=uuid.uuid4().hex, dataset_id=request.dataset_id, tests=tests, request=request,
                  progress={t: {"done": 0, "total": 0} for t in tests})
        with self._lock:
            self._purge()
            self._jobs[job.id] = job
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            self._purge()
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job is not None and job.status in ACTIVE:
            job.cancel.set()
            if job.status == "queued":           # never picked up — finish it here
                self._finish(job, "cancelled")
        return job

    def shutdown(self) -> None:
        with self._lock:
            for job in self._jobs.values():
                job.cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _purge(self) -> None:
        now = time.time()
        expired = [j.id for j in self._jobs.values()
                   if j.finished is not None and now - j.finished > self._ttl_s]
        for job_id in expired:
            del self._jobs[job_id]

    def _finish(self, job: Job, status: str) -> None:
        job.status = status
        job.finished = time.time()
        job.request = None                        # drop the input data

    def _run(self, job: Job) -> None:
        if job.cancel.is_set():
            return
        job.status = "running"
        request = job.request
        for test in job.tests:
            if job.cancel.is_set():
                break
            runner = RUNNERS.get(test)
            if runner is None:
                job.errors[test] = "not implemented yet"
                continue

            def progress(done: int, total: int, _t: str = test) -> None:
                job.progress[_t] = {"done": done, "total": total}

            ctx = RunContext(settings=request.settings, n_jobs=self._n_jobs,
                             cancel=job.cancel, progress=progress)
            try:
                job.results[test] = to_jsonable(runner(getattr(request, test), ctx))
            except Cancelled:
                break
            except Exception as exc:              # one test failing must not lose the others
                log.exception("Job %s: test %s failed", job.id, test)
                job.errors[test] = f"{type(exc).__name__}: {exc}"

        if job.cancel.is_set():
            self._finish(job, "cancelled")
        else:
            self._finish(job, "done" if job.results else "failed")

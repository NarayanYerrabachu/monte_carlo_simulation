"""Job store: one thread pool, one job per request, tests run in sequence.

A job's tests run one after another; a failing test is recorded in
``errors`` and the others still run (partial results are useful). The input
data is dropped as soon as the job ends — only results stay, for
``MC_JOB_TTL_S`` seconds (7 days by default). The records of a fleet or
scenario job are kept for ``MC_RERUN_TTL_S`` (1 h) so the report page can
re-run it with other parameters.

With ``MC_JOB_DIR`` set, every finished job (result + live feed, gzip JSON)
is saved there and loaded again at startup, so report and replay links keep
working after the container is restarted or rebuilt.
"""
from __future__ import annotations

import gzip
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mc_service.contract import SimulationRequest, SimulationResponse
from mc_service.engine import Cancelled
from mc_service.live import LiveFeed
from mc_service.serialize import to_jsonable
from mc_service.simulations import RUNNERS, RunContext

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
RERUNNABLE = ("fleet", "scenario")          # tests whose records are kept for what-if re-runs


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
    live: dict[str, LiveFeed] = field(default_factory=dict)
    rerun_input: SimulationRequest | None = None   # fleet / scenario request kept for what-if re-runs (until TTL)

    def status_view(self) -> dict[str, Any]:
        end = self.finished or time.time()
        return {
            "job_id": self.id,
            "dataset_id": self.dataset_id,
            "status": self.status,
            "created": self.created,
            "tests": self.tests,
            "progress": self.progress,
            "errors": self.errors,
            "elapsed_s": round(end - self.created, 3),
        }

    def live_view(self, since: int = 0) -> dict[str, Any]:
        return {**self.status_view(), "live": {t: feed.snapshot(since) for t, feed in self.live.items()}}

    def response(self) -> dict[str, Any]:
        return SimulationResponse(dataset_id=self.dataset_id, job_id=self.id,
                                  errors=self.errors, **self.results).model_dump()


class JobStore:
    def __init__(self, workers: int, n_jobs: int, ttl_s: float, job_dir: str | None = None,
                 rerun_ttl_s: float = 3600):
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mc-job")
        self._n_jobs = n_jobs
        self._ttl_s = ttl_s
        self._rerun_ttl_s = rerun_ttl_s
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._dir = Path(job_dir) if job_dir else None
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._load()

    # ── persistence ──────────────────────────────────────────────────────────
    def _path(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.json.gz"

    def _save(self, job: Job, status: str) -> None:
        """Write the finished job (called before it is marked finished, so a client that sees
        'done' can rely on the saved copy)."""
        if self._dir is None or status not in ("done", "failed"):
            return
        doc = {"id": job.id, "dataset_id": job.dataset_id, "tests": job.tests, "status": status,
               "created": job.created, "finished": job.finished, "progress": job.progress,
               "results": job.results, "errors": job.errors,
               "live": {t: feed.to_dict() for t, feed in job.live.items()}}
        tmp = self._path(job.id).with_suffix(".tmp")
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(to_jsonable(doc), fh)
            tmp.replace(self._path(job.id))
        except OSError as exc:                    # a full / read-only disk must not fail the job
            log.warning("Job %s: could not save to %s: %s", job.id, self._dir, exc)

    def _load(self) -> None:
        now, loaded = time.time(), 0
        for stray in self._dir.glob("*.tmp"):              # a write cut short by a restart
            stray.unlink(missing_ok=True)
        for path in self._dir.glob("*.json.gz"):
            try:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    doc = json.load(fh)
                if doc.get("finished") and now - doc["finished"] > self._ttl_s:
                    path.unlink(missing_ok=True)
                    continue
                job = Job(id=doc["id"], dataset_id=doc["dataset_id"], tests=doc["tests"], request=None,
                          status=doc["status"], created=doc["created"], finished=doc["finished"],
                          progress=doc["progress"], results=doc["results"], errors=doc["errors"],
                          live={t: LiveFeed.from_dict(f) for t, f in doc.get("live", {}).items()})
                self._jobs[job.id] = job
                loaded += 1
            except (OSError, ValueError, KeyError) as exc:
                log.warning("Skipping unreadable saved job %s: %s", path.name, exc)
        log.info("Loaded %d saved job(s) from %s", loaded, self._dir)

    def submit(self, request: SimulationRequest) -> Job:
        tests = request.requested_tests()
        job = Job(id=uuid.uuid4().hex, dataset_id=request.dataset_id, tests=tests, request=request,
                  progress={t: {"done": 0, "total": 0} for t in tests},
                  live={t: LiveFeed() for t in tests})
        with self._lock:
            self._purge()
            self._jobs[job.id] = job
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            self._purge()
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        with self._lock:
            self._purge()
            return sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)

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
            if self._dir is not None:
                self._path(job_id).unlink(missing_ok=True)
        for j in self._jobs.values():             # the records for re-runs are large: keep them 1 h
            if j.rerun_input is not None and j.finished and now - j.finished > self._rerun_ttl_s:
                j.rerun_input = None

    def _finish(self, job: Job, status: str) -> None:
        job.finished = time.time()
        self._save(job, status)                   # saved first: 'done' then really means safe on disk
        job.status = status
        # Drop the input data — except a fleet / scenario request, kept (until the TTL) so
        # the dashboard can re-run it with other parameters without the sender.
        if job.request is not None and any(getattr(job.request, t) is not None for t in RERUNNABLE):
            job.rerun_input = job.request
        job.request = None

    def rerun(self, job: Job, n_sims: int | None, overrides: dict[str, dict]) -> Job:
        """New job on the same records with changed parameters (``overrides``: test → fields)."""
        base = job.rerun_input or job.request
        test = next((t for t in RERUNNABLE if t in job.tests), None)
        if test is None:
            raise ValueError("This job has no records to re-run (only fleet and scenario jobs can be re-run).")
        if base is None or getattr(base, test) is None:
            raise ValueError("This job's records are no longer held for re-runs (they are kept for "
                             "1 hour, and not across restarts). Press Monte Carlo ↗ in CortXplorer again "
                             "to send the data, then re-run.")
        section = getattr(base, test)
        changes = dict(overrides.get(test) or {})
        if test == "scenario" and "alerts" in changes:          # per-metric alert thresholds
            alerts = changes.pop("alerts") or {}
            changes["metrics"] = [m.model_copy(update={"alert": alerts[m.key]}) if m.key in alerts else m
                                  for m in section.metrics]
        section = section.model_copy(update=changes)
        settings = base.settings.model_copy(update={"n_sims": n_sims} if n_sims else {})
        request = SimulationRequest(contract_version=base.contract_version, dataset_id=base.dataset_id,
                                    settings=settings, **{test: type(section).model_validate(section.model_dump())})
        return self.submit(request)

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
                             cancel=job.cancel, progress=progress, live=job.live[test])
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

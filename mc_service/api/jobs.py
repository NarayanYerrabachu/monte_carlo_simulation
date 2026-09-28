"""Job API: submit a simulation request, poll it, fetch the result, cancel it."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from mc_service.api.envelope import EnvelopeRoute
from mc_service.contract import SimulationRequest
from mc_service.jobs import ACTIVE, Job, JobStore

router = APIRouter(prefix="/v1/jobs", route_class=EnvelopeRoute, tags=["jobs"])


def _store(request: Request) -> JobStore:
    return request.app.state.jobs


def _job(request: Request, job_id: str) -> Job:
    job = _store(request).get(job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found (unknown or expired)")
    return job


@router.post("", status_code=202)
def submit(body: SimulationRequest, request: Request):
    job = _store(request).submit(body)
    return {"job_id": job.id, "status": job.status, "tests": job.tests}


@router.get("")
def list_jobs(request: Request):
    return [job.status_view() for job in _store(request).list_jobs()]


@router.get("/{job_id}")
def status(job_id: str, request: Request):
    return _job(request, job_id).status_view()


@router.get("/{job_id}/live")
def live(job_id: str, request: Request, since: int = Query(0, ge=0)):
    """Status plus, per test, the null values from index ``since`` on and the
    latest surrogate. Poll with ``since`` = the previous ``n_null``."""
    return _job(request, job_id).live_view(since)


@router.get("/{job_id}/result")
def result(job_id: str, request: Request):
    job = _job(request, job_id)
    if job.status in ACTIVE:
        raise HTTPException(409, f"Job {job_id} is still {job.status}")
    if job.status == "cancelled":
        raise HTTPException(409, f"Job {job_id} was cancelled")
    return job.response()


@router.delete("/{job_id}")
def cancel(job_id: str, request: Request):
    _job(request, job_id)
    return _store(request).cancel(job_id).status_view()

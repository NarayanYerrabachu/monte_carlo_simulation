"""Fleet-management report API — JSON in, JSON out.

Every request is a JSON body (``FleetReportRequest``) and every response is the
``{status, data, message}`` envelope. The PDF and Excel files travel inside the
JSON as base64 (``ReportFile``); the frontend decodes them into a download.
All three come from the same ``build_report(n, seed)`` run (cached per
(n, seed)), so numbers and texts are identical across formats.
"""
from __future__ import annotations

import base64
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from mc_service import job_report
from mc_service.api.envelope import EnvelopeRoute
from mc_service.fleet.excel import build_xlsx
from mc_service.fleet.model import build_report
from mc_service.fleet.pdf import build_pdf

router = APIRouter(prefix="/v1/reports", route_class=EnvelopeRoute, tags=["reports"])

PDF_TYPE = "application/pdf"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class FleetReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int = Field(10_000, ge=1_000, le=50_000, description="simulated operating days")
    seed: int = Field(42, ge=0, description="random seed (same seed → same report)")


class JobReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1)
    format: Literal["json", "pdf", "xlsx"] = "json"


def _encoded(content: bytes, content_type: str, filename: str) -> dict:
    return {"filename": filename, "content_type": content_type, "encoding": "base64",
            "size_bytes": len(content), "content": base64.b64encode(content).decode("ascii")}


@router.post("/job")
def job_report_file(body: JobReportRequest, request: Request):
    """Report of a finished job (fleet simulation or loop test): the result as JSON, or PDF / Excel as base64."""
    job = request.app.state.jobs.get(body.job_id)
    if job is None:
        raise HTTPException(404, f"Job {body.job_id} not found (unknown or expired)")
    if job.status not in ("done", "failed"):
        raise HTTPException(409, f"Job {body.job_id} is {job.status} — the report is available when it is done")
    result = job.response()
    try:
        kind = job_report.kind(result)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if body.format == "json":
        return {"kind": kind, "result": result}
    name = f"monte-carlo-{kind}-{body.job_id[:8]}.{body.format}"
    if body.format == "pdf":
        return _encoded(job_report.build_pdf(result), PDF_TYPE, name)
    return _encoded(job_report.build_xlsx(result), XLSX_TYPE, name)


def _file(content: bytes, content_type: str, body: FleetReportRequest, ext: str) -> dict:
    """A generated file as JSON: name, type, size and base64 content."""
    return {
        "filename": f"fleet-monte-carlo-{body.n}-seed{body.seed}.{ext}",
        "content_type": content_type,
        "encoding": "base64",
        "size_bytes": len(content),
        "content": base64.b64encode(content).decode("ascii"),
    }


@router.post("/fleet")
def fleet_report(body: FleetReportRequest):
    return build_report(body.n, body.seed).data


@router.post("/fleet/pdf")
def fleet_report_pdf(body: FleetReportRequest):
    return _file(build_pdf(build_report(body.n, body.seed)), PDF_TYPE, body, "pdf")


@router.post("/fleet/xlsx")
def fleet_report_xlsx(body: FleetReportRequest):
    return _file(build_xlsx(build_report(body.n, body.seed)), XLSX_TYPE, body, "xlsx")

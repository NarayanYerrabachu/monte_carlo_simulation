"""Fleet-management report: JSON for the HTML page, plus PDF and Excel downloads.

All three are built from the same ``build_report(n, seed)`` result (cached per
(n, seed)), so the numbers and texts are identical across formats.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import Response

from mc_service.api.envelope import EnvelopeRoute
from mc_service.fleet.excel import build_xlsx
from mc_service.fleet.model import build_report
from mc_service.fleet.pdf import build_pdf

router = APIRouter(prefix="/v1/reports", route_class=EnvelopeRoute, tags=["reports"])

N = Query(10_000, ge=1_000, le=50_000, description="simulated operating days")
SEED = Query(42, ge=0, description="random seed (same seed → same report)")


def _download(content: bytes, media_type: str, filename: str) -> Response:
    return Response(content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/fleet")
def fleet_report(n: int = N, seed: int = SEED):
    return build_report(n, seed).data


@router.get("/fleet.pdf")
def fleet_report_pdf(n: int = N, seed: int = SEED):
    return _download(build_pdf(build_report(n, seed)), "application/pdf",
                     f"fleet-monte-carlo-{n}-seed{seed}.pdf")


@router.get("/fleet.xlsx")
def fleet_report_xlsx(n: int = N, seed: int = SEED):
    return _download(build_xlsx(build_report(n, seed)),
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     f"fleet-monte-carlo-{n}-seed{seed}.xlsx")

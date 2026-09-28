"""Monte Carlo simulation service — FastAPI app (port 8020).

Run:  pipenv run uvicorn mc_service.main:app --host 0.0.0.0 --port 8020
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mc_service import CONTRACT_VERSION, __version__
from mc_service.api import jobs as jobs_api
from mc_service.api import reports as reports_api
from mc_service.api.body_limit import BodyLimitMiddleware
from mc_service.api.envelope import EnvelopeRoute, install_error_handlers
from mc_service.config import ServiceConfig
from mc_service.jobs import JobStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

config = ServiceConfig.from_env()
# HTML / JS / CSS live in the repo's frontend/ folder (Python returns JSON or files only)
FRONTEND_DIR = Path(os.getenv("MC_FRONTEND_DIR", Path(__file__).resolve().parents[1] / "frontend"))


class NoCacheStatic(StaticFiles):
    """Viewer assets are tiny; never serve a stale viewer.js after an update."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.jobs = JobStore(workers=config.workers, n_jobs=config.n_jobs, ttl_s=config.job_ttl_s)
    yield
    app.state.jobs.shutdown()


app = FastAPI(title="CortXplorer Monte Carlo Service", version=__version__, lifespan=lifespan)
app.router.route_class = EnvelopeRoute
install_error_handlers(app)
app.add_middleware(BodyLimitMiddleware, max_bytes=config.max_body_bytes)
app.include_router(jobs_api.router)
app.include_router(reports_api.router)
app.mount("/js", NoCacheStatic(directory=FRONTEND_DIR / "js"), name="js")
app.mount("/css", NoCacheStatic(directory=FRONTEND_DIR / "css"), name="css")


@app.get("/", include_in_schema=False)
def viewer():
    """Live viewer (static page; all rendering happens in the browser)."""
    return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/fleet", include_in_schema=False)
def fleet_page():
    """Fleet Monte Carlo dashboard (static page; renders /v1/jobs/{id}/result, re-runs via /rerun)."""
    return FileResponse(FRONTEND_DIR / "fleet.html", headers={"Cache-Control": "no-cache"})


@app.get("/report", include_in_schema=False)
def report_page(job: str | None = None):
    """``/report?job=<id>``: report of that job (HTML + PDF + Excel). Without a job: the
    assumption-based fleet report. Both are static pages that render API JSON."""
    page = "fleet.html" if job else "fleet_report.html"
    return FileResponse(FRONTEND_DIR / page, headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__, "contract_version": CONTRACT_VERSION}

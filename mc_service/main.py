"""Monte Carlo simulation service — FastAPI app (port 8020).

Run:  pipenv run uvicorn mc_service.main:app --host 0.0.0.0 --port 8020
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mc_service import CONTRACT_VERSION, __version__
from mc_service.api import jobs as jobs_api
from mc_service.api.body_limit import BodyLimitMiddleware
from mc_service.api.envelope import EnvelopeRoute, install_error_handlers
from mc_service.config import ServiceConfig
from mc_service.jobs import JobStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

config = ServiceConfig.from_env()
STATIC_DIR = Path(__file__).parent / "static"


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
app.mount("/static", NoCacheStatic(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def viewer():
    """Live viewer (static page; all rendering happens in the browser)."""
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__, "contract_version": CONTRACT_VERSION}

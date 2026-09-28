"""Monte Carlo simulation service — FastAPI app (port 8020).

Run:  pipenv run uvicorn mc_service.main:app --host 0.0.0.0 --port 8020
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mc_service import CONTRACT_VERSION, __version__
from mc_service.api import jobs as jobs_api
from mc_service.api.body_limit import BodyLimitMiddleware
from mc_service.api.envelope import EnvelopeRoute, install_error_handlers
from mc_service.config import ServiceConfig
from mc_service.jobs import JobStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

config = ServiceConfig.from_env()


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


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__, "contract_version": CONTRACT_VERSION}

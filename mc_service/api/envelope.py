"""Uniform JSON envelope for every response — same shape as the CortXplorer demo
(``backend/api/envelope.py``), so the demo client handles both alike.

Success:  {"status": "success", "data": <payload>, "message": null}
Error:    {"status": "error",   "data": null,      "message": "<why>"}
"""
from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


def success(data: Any, message: str | None = None) -> dict:
    return {"status": "success", "data": data, "message": message}


def error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code,
                        content={"status": "error", "data": None, "message": message})


def _wrap(value: Any) -> Any:
    return value if isinstance(value, Response) else success(value)


def _enveloped(endpoint: Callable) -> Callable:
    if inspect.iscoroutinefunction(endpoint):
        @functools.wraps(endpoint)
        async def async_wrapper(*args, **kwargs):
            return _wrap(await endpoint(*args, **kwargs))
        return async_wrapper

    @functools.wraps(endpoint)
    def sync_wrapper(*args, **kwargs):
        return _wrap(endpoint(*args, **kwargs))
    return sync_wrapper


class EnvelopeRoute(APIRoute):
    """APIRoute whose return values are wrapped in the success envelope (all routes;
    FastAPI's own /docs and /openapi.json are not APIRoutes and stay untouched)."""

    def __init__(self, path: str, endpoint: Callable, **kwargs):
        super().__init__(path, _enveloped(endpoint), **kwargs)


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "Invalid request — " + "; ".join(parts)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        return error(str(exc.detail), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return error(_validation_message(exc), 422)

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception):
        log.error("Unhandled error on %s %s: %s", request.method, request.url.path, exc, exc_info=exc)
        return error(f"{type(exc).__name__}: {exc}", 500)

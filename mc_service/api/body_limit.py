"""ASGI middleware: request size limit and gzip request bodies.

The demo sends feature matrices of tens of thousands of rows, so it gzips the
JSON (``Content-Encoding: gzip``). Both the compressed and the decompressed
size are capped at ``max_bytes`` (a small gzip bomb cannot exhaust memory).
"""
from __future__ import annotations

import zlib

from fastapi.responses import JSONResponse


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code,
                        content={"status": "error", "data": None, "message": message})


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope["headers"]}
        limit_mb = self.max_bytes / 1024 / 1024
        too_large = _error(f"Request body larger than {limit_mb:g} MB", 413)
        if int(headers.get(b"content-length", b"0") or 0) > self.max_bytes:
            await too_large(scope, receive, send)
            return

        chunks, size, more = [], 0, True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                await too_large(scope, receive, send)
                return
            chunks.append(chunk)
            more = message.get("more_body", False)
        body = b"".join(chunks)

        encoding = headers.get(b"content-encoding", b"").strip().lower()
        if encoding == b"gzip":
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            try:
                body = inflater.decompress(body, self.max_bytes)
            except zlib.error:
                await _error("Request body is not valid gzip", 400)(scope, receive, send)
                return
            if inflater.unconsumed_tail:
                await too_large(scope, receive, send)
                return
        elif encoding not in (b"", b"identity"):
            await _error(f"Unsupported Content-Encoding {encoding.decode()!r}", 415)(scope, receive, send)
            return

        scope = dict(scope)
        scope["headers"] = [(k, v) for k, v in scope["headers"]
                            if k.lower() not in (b"content-encoding", b"content-length")]
        scope["headers"].append((b"content-length", str(len(body)).encode()))

        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

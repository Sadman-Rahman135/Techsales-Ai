"""Counts every response by route template and status (§13.1), for app/core/request_metrics.py."""

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from starlette.responses import Response

from app.core.request_metrics import counter


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def count(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.perf_counter()
        status = 500  # an exception that escapes every handler is a 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", None) or "unmatched"
            counter.record(request.method, route, status, round((time.perf_counter() - started) * 1000))

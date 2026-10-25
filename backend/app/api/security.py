"""Response hardening for every API route (blueprint §11.2).

- Browser safety headers on every response; JSON is never cached (it is per-user data).
- HSTS outside development: TLS terminates in front of the API (§16), and browsers must never fall back.
- The interactive API docs and schema are for developers: hidden in staging and production. The voice contract
  is published as a frozen file instead (docs/contracts/).
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.config import get_settings

DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})
HSTS = "max-age=31536000; includeSubDomains"


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def harden(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # Through the dependency override when tests set one, so deployed behaviour is testable.
        deployed = request.app.dependency_overrides.get(get_settings, get_settings)().deployed
        if deployed and request.url.path in DOCS_PATHS:
            return JSONResponse(
                {"error": {"code": "NOT_FOUND", "message": "Not found.", "request_id": "req-docs"}},
                status_code=status.HTTP_404_NOT_FOUND,
            )
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        if headers.get("content-type", "").startswith("application/json"):
            headers.setdefault("Cache-Control", "no-store")
        if deployed:
            headers.setdefault("Strict-Transport-Security", HSTS)
        return response

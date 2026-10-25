"""One error shape for every response (blueprint §9.3) and a request ID on every request (§9, §13).

    {"error": {"code": "FILE_TOO_LARGE", "message": "…", "request_id": "req-…", "details": {…}}}

`message` is safe to show an admin. Customer-facing screens map `code` to copy from
design-system/content-guide.md and show only the request ID.
"""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.services import versions

log = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Every non-2xx response (§9.3)."""

    error: ErrorBody


def _documented(description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


# Shown in OpenAPI on every router: clients see the §9.3 shape, not FastAPI's default validation schema.
COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    400: _documented("Malformed request (body is not valid JSON)"),
    401: _documented("Not signed in, or the token/session is invalid"),
    403: _documented("Signed in, but not allowed"),
    404: _documented("Absent, or hidden from this caller (no existence leak)"),
    422: _documented("Validation failed; `details.fields` lists the fields"),
    429: _documented("Rate limited; see the Retry-After header"),
}

# For routes whose success response isn't JSON (SSE): FastAPI would list errors under the route's media type,
# but refusals happen before streaming starts and are plain JSON.
JSON_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {
        "description": spec["description"],
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
    }
    for code, spec in COMMON_ERRORS.items()
}
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}  # e.g. Retry-After on 429


# Domain errors from app.services.versions → HTTP status.
_VERSION_STATUS: dict[type[versions.VersionError], int] = {
    versions.PermissionDenied: status.HTTP_403_FORBIDDEN,
    versions.NotFound: status.HTTP_404_NOT_FOUND,
    versions.InvalidTransition: status.HTTP_409_CONFLICT,
    versions.VersionNotIndexed: status.HTTP_409_CONFLICT,
}

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "FILE_TOO_LARGE",
    429: "RATE_LIMITED",
}


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req-unknown")


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id(request)}
    if details:
        body["details"] = details
    headers = {REQUEST_ID_HEADER: request_id(request), **(extra_headers or {})}
    if status_code == status.HTTP_401_UNAUTHORIZED:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse({"error": body}, status_code=status_code, headers=headers)


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        # Accept the BFF's ID so one request can be traced end to end; otherwise mint one.
        request.state.request_id = (
            incoming if _VALID_REQUEST_ID.fullmatch(incoming) else f"req-{uuid.uuid4().hex}"
        )
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(versions.VersionError)
    async def version_error(request: Request, exc: versions.VersionError) -> JSONResponse:
        code = _VERSION_STATUS.get(type(exc), status.HTTP_409_CONFLICT)
        return error_response(request, code, exc.code, str(exc))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "ERROR")
        return error_response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # §9.3: a body that isn't valid JSON at all is malformed (400); well-formed but invalid input is 422.
        if any(err.get("type") == "json_invalid" for err in exc.errors()):
            return error_response(
                request,
                status.HTTP_400_BAD_REQUEST,
                "MALFORMED_REQUEST",
                "The request body isn't valid JSON.",
            )
        # Field locations and messages only: never echo the submitted values back.
        fields = [
            {"field": ".".join(str(p) for p in err["loc"] if p != "body"), "message": err["msg"]}
            for err in exc.errors()
        ]
        return error_response(
            request,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "VALIDATION_ERROR",
            "Some fields are missing or invalid.",
            {"fields": fields},
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"request_id": request_id(request)})
        return error_response(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "Something went wrong on our side.",
        )

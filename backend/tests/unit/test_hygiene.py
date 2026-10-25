"""§11.2 hygiene without the stack: log scrubbing, response headers, hidden docs, the sign-in lock."""

import io
import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import health
from app.api.ratelimit import FailureLock
from app.config import AppEnv, Settings, get_settings
from app.core.log_hygiene import RedactingFilter, install_redaction, scrub
from app.main import app

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlLXZhbHVl"


@pytest.mark.parametrize(
    ("raw", "leak"),
    [
        (f"Authorization: Bearer {JWT}", JWT),
        (f'"GET /api/v1/files/{JWT} HTTP/1.1" 200', JWT),
        ('"GET /api/v1/files/abc.def-ghi HTTP/1.1" 200', "abc.def-ghi"),
        ("connect postgresql://app:hunter2-secret@db.internal/k failed", "hunter2-secret"),
        ("provider said: invalid key sk-proj-abcdefghijklmnop1234", "sk-proj-abcdefghijklmnop1234"),
        ('"GET /api/v1/documents?q=jordan%20smith&limit=25 HTTP/1.1"', "jordan%20smith"),
        ("question from jordan@example.com about TC-123456", "jordan@example.com"),
        ("call me on +1 312 555 0182", "555 0182"),
        ("card 4111 1111 1111 1111 declined", "4111 1111 1111 1111"),
    ],
)
def test_secrets_and_personal_data_never_reach_the_log(raw: str, leak: str) -> None:
    assert leak not in scrub(raw)


def test_ordinary_log_lines_are_left_alone() -> None:
    line = "job 019a1541-5e11-77ac-8745-4163ee6a496a: done {'parsing': 12, 'embedding_tokens': 1834}"
    assert scrub(line) == line
    assert scrub("reconcile: requeued=0 failed=0 files=2") == "reconcile: requeued=0 failed=0 files=2"


def test_the_filter_scrubs_arguments_and_tracebacks() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("test.hygiene")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning("login failed for %s", "jordan@example.com")
        try:
            raise ValueError(f"bad token {JWT}")
        except ValueError:
            logger.exception("upstream error")
    finally:
        logger.removeHandler(handler)
    output = stream.getvalue()
    assert "jordan@example.com" not in output and JWT not in output
    assert "[email]" in output and "[jwt]" in output and "ValueError" in output


def test_uvicorn_access_lines_still_format_and_are_scrubbed() -> None:
    from uvicorn.logging import AccessFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    )
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("test.uvicorn.access")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        # uvicorn's own call shape: a format string and a 5-tuple the formatter unpacks.
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", f"/api/v1/files/{JWT}", "1.1", 200)
    finally:
        logger.removeHandler(handler)
    line = stream.getvalue()
    assert line.strip() == '127.0.0.1:5000 - "GET /api/v1/files/[token] HTTP/1.1" 200 OK'


def test_install_redaction_covers_root_and_is_idempotent() -> None:
    install_redaction()
    install_redaction()
    root = logging.getLogger()
    assert root.handlers
    for handler in root.handlers:
        assert sum(isinstance(f, RedactingFilter) for f in handler.filters) == 1


# ---------- response hardening ----------


def _settings(tmp_path: Path, env: AppEnv) -> Settings:
    base = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        storage_local_dir=tmp_path,
        embedding_provider="fake",
        embedding_model="fake-embedding",
        embedding_dimension=384,
        llm_provider="fake",
        llm_model="fake-llm",
        auth_jwt_secret="a" * 40,
        internal_service_token="b" * 40,
    )
    return base.model_copy(update={"app_env": env})  # skip the deployed rules: only the flag matters here


@pytest.fixture
def client_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[AppEnv], TestClient]]:
    monkeypatch.setattr(health, "CHECKS", {"storage": health.check_storage})

    def make(env: AppEnv) -> TestClient:
        settings = _settings(tmp_path, env)
        app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def test_every_response_carries_safety_headers(client_for: Callable[[AppEnv], TestClient]) -> None:
    client = client_for(AppEnv.DEVELOPMENT)
    response = client.get("/api/v1/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in response.headers  # plain HTTP in development
    assert client.get("/openapi.json").status_code == 200


@pytest.mark.parametrize("env", [AppEnv.STAGING, AppEnv.PRODUCTION])
def test_deployed_environments_add_hsts_and_hide_the_docs(
    client_for: Callable[[AppEnv], TestClient], env: AppEnv
) -> None:
    client = client_for(env)
    response = client.get("/api/v1/health")
    assert response.headers["strict-transport-security"].startswith("max-age=31536000")
    for path in ("/docs", "/redoc", "/openapi.json"):
        hidden = client.get(path)
        assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "NOT_FOUND"


# ---------- sign-in lock ----------


def test_failure_lock_locks_after_the_limit_and_expires() -> None:
    lock = FailureLock()
    for second in range(5):
        assert lock.locked_for("a", limit=5, window=900, now=second) is None
        lock.fail("a", window=900, now=second)
    assert lock.locked_for("a", limit=5, window=900, now=10) == 890  # window - (now - first failure)
    assert lock.locked_for("b", limit=5, window=900, now=10) is None  # per account
    assert lock.locked_for("a", limit=5, window=900, now=901) is None  # the oldest failure aged out
    lock.clear("a")
    assert lock.locked_for("a", limit=1, window=900, now=902) is None


def test_the_api_grants_no_cross_origin_access() -> None:
    """§16 CORS: browsers talk only to the BFF (ADR-005), so the API sends no CORS headers at all: a page on
    another origin can neither read its responses nor get a preflight approved."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    preflight = client.options(
        "/api/v1/chat/ask",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in preflight.headers
    simple = client.get("/api/v1/health/live", headers={"Origin": "https://evil.example"})
    assert simple.status_code == 200 and "access-control-allow-origin" not in simple.headers

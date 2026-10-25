"""Blueprint §5.1: Postgres, Chroma and storage run locally. Needs `docker compose up -d` and `.env`."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.integration


def test_local_stack_is_healthy() -> None:
    with TestClient(app) as client:  # runs lifespan: settings validation + storage dir
        response = client.get("/api/v1/health")
    body = response.json()
    checks = {c["name"]: c["ok"] for c in body["checks"]}
    assert set(checks) == {"postgres", "chroma", "storage", "worker", "queue"}
    assert checks["postgres"] and checks["chroma"] and checks["storage"], body
    # The worker is a separate process, which a test run doesn't start: its check is reported either way
    # (test_operations covers a running and a stopped worker).
    assert response.status_code == (200 if all(checks.values()) else 503)

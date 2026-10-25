from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import health
from app.config import Settings, get_settings
from app.main import app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
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
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def _ok(settings: Settings) -> None:
    return None


def _down(settings: Settings) -> None:
    raise ConnectionRefusedError("db.internal:5432 password=secret")


def test_all_components_up_returns_200(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "CHECKS", {"postgres": _ok, "chroma": _ok, "storage": health.check_storage})
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [c["name"] for c in body["checks"]] == ["postgres", "chroma", "storage"]


def test_component_down_returns_503_without_leaking_details(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "CHECKS", {"postgres": _down, "chroma": _ok, "storage": _ok})
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    postgres = response.json()["checks"][0]
    assert postgres == {
        "name": "postgres",
        "ok": False,
        "latency_ms": postgres["latency_ms"],
        "detail": "ConnectionRefusedError",
    }
    assert "secret" not in response.text and "db.internal" not in response.text


def test_liveness_answers_even_when_every_component_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "CHECKS", {"postgres": _down, "chroma": _down, "storage": _down})
    assert client.get("/api/v1/health").status_code == 503
    live = client.get("/api/v1/health/live")
    assert live.status_code == 200 and live.json() == {"status": "ok"}

"""§16 smoke test client: it fails loudly, and a failure after the upload still removes the test document."""

import json

import httpx
import pytest

from app.cli import smoke

ENV = {
    "SMOKE_MANAGER_EMAIL": "m@example.test",
    "SMOKE_MANAGER_PASSWORD": "pw",
    "SMOKE_ADMIN_EMAIL": "a@example.test",
    "SMOKE_ADMIN_PASSWORD": "pw",
}


def test_missing_credentials_fail_before_any_request(capsys: pytest.CaptureFixture[str]) -> None:
    assert smoke.run("http://api.invalid", {}) is False
    assert "SMOKE_MANAGER_EMAIL" in capsys.readouterr().out


def test_a_failed_step_stops_the_run_and_deletes_the_test_document(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/health/live"):
            return httpx.Response(200, json={"status": "ok"})
        if path.endswith("/health"):
            return httpx.Response(200, json={"status": "ok", "app_env": "test", "checks": []})
        if path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "t"})
        if path.endswith("/documents") and request.method == "POST":
            return httpx.Response(
                202, json={"document_id": "d1", "version_id": "v1", "job": {"id": "j1", "state": "queued"}}
            )
        if "/ingestion-jobs/" in path:
            return httpx.Response(200, json={"state": "done"})
        if path.endswith("/publish"):
            return httpx.Response(200, json={})
        if path.endswith("/chat/ask"):
            return httpx.Response(
                200, json={"answer_status": "insufficient_evidence", "answer": "", "citations": []}
            )
        if request.method == "DELETE":
            deleted.append(path)
            return httpx.Response(204)
        return httpx.Response(404, content=json.dumps({}))

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    assert smoke.run("http://api.test", ENV) is False
    out = capsys.readouterr().out
    assert "PASS health" in out and "PASS upload + publish" in out
    assert "FAIL ask + citation open" in out and "revoke" not in out  # stops at the first failure
    assert deleted == ["/api/v1/documents/d1"]  # never leaves a live test document behind
    assert "pw" not in out  # credentials are never printed
    assert smoke.word().isalpha() and smoke.word() != smoke.word()

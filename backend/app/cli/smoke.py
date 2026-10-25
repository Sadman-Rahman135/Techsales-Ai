"""Post-deploy smoke test (blueprint §16): health, upload, ask, citation open, publish, revoke, internal API.

A plain HTTP client: run it from anywhere that can reach the API, after every deploy and after a rollback.
It creates one fictional public document with a unique made-up word, proves the whole path with it, then
deletes it again and checks that its answers and its file are gone (§16 "verify file deletion and
source-access behavior after deploy"). Credentials come from the environment and are never printed.

    SMOKE_MANAGER_EMAIL=… SMOKE_MANAGER_PASSWORD=… SMOKE_ADMIN_EMAIL=… SMOKE_ADMIN_PASSWORD=… \\
    INTERNAL_SERVICE_TOKEN=…   # optional: also checks the voice endpoint (from an allowed network)
    python -m app.cli.smoke --base-url https://api.example.internal

Exits 0 when every step passes, 1 otherwise. Needs the worker running (the upload is processed).

Rollback drills (docs/deployment.md §7) keep one document across a switch and check it on both sides:

    python -m app.cli.smoke --keep                    # the full run without the delete; prints the ID
    python -m app.cli.smoke --check-document <id>     # health + the kept document still answers and is cited
    python -m app.cli.smoke --revoke-document <id>    # delete it at the end of the drill
"""

import argparse
import os
import secrets
import string
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

V1 = "/api/v1"
QUESTION = "What is the smoke check word for this deployment?"


class SmokeFailure(Exception):
    pass


def word() -> str:
    """A unique made-up word: letters only, so it is an ordinary search term, never a "code" (ADR-024)."""
    return "smoke" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(8))


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


class Smoke:
    def __init__(self, client: httpx.Client, env: dict[str, str]) -> None:
        self.client = client
        self.env = env
        self.secret_word = word()
        self.session = {"X-Session-Ref": secrets.token_urlsafe(24)}
        self.manager: dict[str, str] = {}
        self.admin: dict[str, str] = {}
        self.document_id = ""
        self.file_url = ""
        self.keep = False  # --keep: a successful run leaves its document for a later --check/--revoke

    def sign_in(self, role: str) -> dict[str, str]:
        response = self.client.post(
            f"{V1}/auth/login",
            json={"email": self.env[f"SMOKE_{role}_EMAIL"], "password": self.env[f"SMOKE_{role}_PASSWORD"]},
        )
        expect(response.status_code == 200, f"{role.lower()} sign-in returned {response.status_code}")
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def ask(self, question: str) -> dict[str, Any]:
        response = self.client.post(f"{V1}/chat/ask", json={"question": question}, headers=self.session)
        expect(response.status_code == 200, f"ask returned {response.status_code}")
        result: dict[str, Any] = response.json()
        return result

    # ---------- steps ----------

    def health(self) -> str:
        live = self.client.get(f"{V1}/health/live")
        ready = self.client.get(f"{V1}/health")
        failing = [c["name"] for c in ready.json().get("checks", []) if not c["ok"]]
        expect(live.status_code == 200, f"liveness returned {live.status_code}")
        expect(ready.status_code == 200, f"readiness returned {ready.status_code}; failing: {failing}")
        return f"live, ready ({ready.json()['app_env']})"

    def upload(self) -> str:
        self.manager = self.sign_in("MANAGER")
        self.admin = self.sign_in("ADMIN")
        body = (
            "# Smoke test notice\n\nThe smoke check word for this deployment is "
            f"{self.secret_word}.\n\nThis is a fictional document for the TechCare demo.\n"
        ).encode()
        response = self.client.post(
            f"{V1}/documents",
            headers=self.manager,
            files={"file": (f"{self.secret_word}.txt", body, "text/plain")},
            data={"title": f"Smoke test {self.secret_word}", "audience": "public", "tags": "smoke-test"},
        )
        expect(response.status_code == 202, f"upload returned {response.status_code}: {response.text[:200]}")
        accepted = response.json()
        self.document_id, version_id, job_id = (
            accepted["document_id"],
            accepted["version_id"],
            accepted["job"]["id"],
        )
        deadline = time.monotonic() + 120
        state = "queued"
        while time.monotonic() < deadline:
            state = self.client.get(f"{V1}/ingestion-jobs/{job_id}", headers=self.manager).json()["state"]
            if state in {"done", "failed", "cancelled"}:
                break
            time.sleep(1)
        expect(state == "done", f"ingestion ended as {state!r} (is the worker running?)")
        published = self.client.post(
            f"{V1}/documents/{self.document_id}/publish",
            headers=self.manager,
            json={"version_id": version_id},
        )
        expect(published.status_code == 200, f"publish returned {published.status_code}")
        return f"processed and published document {self.document_id}"

    def answer(self) -> str:
        result = self.ask(QUESTION)
        expect(result["answer_status"] == "answered", f"answer_status {result['answer_status']}")
        expect(self.secret_word in result["answer"], "the answer doesn't contain the document's word")
        cited = [c for c in result["citations"] if c["document_id"] == self.document_id]
        expect(bool(cited), "the answer doesn't cite the new document")
        preview = self.client.get(cited[0]["preview_url"], headers=self.session)
        expect(preview.status_code == 200, f"citation preview returned {preview.status_code}")
        self.file_url = preview.json()["url"]
        original = self.client.get(self.file_url)
        expect(original.status_code == 200, f"cited file returned {original.status_code}")
        expect(self.secret_word in original.text, "the cited file isn't the uploaded original")
        return "answered and cited; the source link opens the original"

    def internal(self) -> str:
        token = self.env.get("INTERNAL_SERVICE_TOKEN")
        if not token:
            return "skipped (INTERNAL_SERVICE_TOKEN not set)"
        response = self.client.post(
            f"{V1}/internal/knowledge/answer",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "question": QUESTION,
                "conversation_id": f"smoke-{secrets.token_hex(6)}",
                "request_id": "smoke-test",
            },
        )
        expect(response.status_code == 200, f"internal API returned {response.status_code}")
        body = response.json()
        expect(body["answer_status"] == "answered", f"voice answer_status {body['answer_status']}")
        expect(
            any(c["document_id"] == self.document_id for c in body["citations"]),
            "voice answer lacks the citation",
        )
        return f"voice contract {body['contract_version']} answered and cited"

    def existing(self) -> str:
        result = self.ask(QUESTION)
        expect(result["answer_status"] == "answered", f"answer_status {result['answer_status']}")
        expect(
            any(c["document_id"] == self.document_id for c in result["citations"]),
            f"the answer doesn't cite document {self.document_id}",
        )
        return f"answered from document {self.document_id}"

    def kept(self) -> str:
        self.keep = True
        return f"KEPT document {self.document_id}"

    def revoke(self) -> str:
        if not self.admin:
            self.admin = self.sign_in("ADMIN")
        deleted = self.client.delete(f"{V1}/documents/{self.document_id}", headers=self.admin)
        expect(deleted.status_code == 204, f"delete returned {deleted.status_code}")
        if self.file_url:
            link = self.client.get(self.file_url)
            expect(link.status_code == 404, f"the old file link still returns {link.status_code}")
        result = self.ask(QUESTION)
        expect(
            result["answer_status"] != "answered" or self.secret_word not in result["answer"], "still answers"
        )
        expect(
            all(c["document_id"] != self.document_id for c in result["citations"]),
            "a deleted document is still cited",
        )
        self.document_id = ""
        return f"deleted: link gone, no longer answered ({result['answer_status']})"

    def cleanup(self) -> None:
        if self.document_id and self.admin and not self.keep:  # a step failed: don't leave the document live
            self.client.delete(f"{V1}/documents/{self.document_id}", headers=self.admin)


def run(
    base_url: str,
    env: dict[str, str],
    timeout: float = 30.0,
    *,
    keep: bool = False,
    check_document: str | None = None,
    revoke_document: str | None = None,
) -> bool:
    required = ["SMOKE_MANAGER_EMAIL", "SMOKE_MANAGER_PASSWORD", "SMOKE_ADMIN_EMAIL", "SMOKE_ADMIN_PASSWORD"]
    missing = [name for name in required if not env.get(name)]
    if missing:
        print(f"missing environment variables: {', '.join(missing)}")
        return False
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        smoke = Smoke(client, env)
        steps: list[tuple[str, Callable[[], str]]]
        if check_document or revoke_document:
            smoke.document_id = check_document or revoke_document or ""
            steps = [("health", smoke.health)]
            if check_document:
                steps.append(("kept document answers", smoke.existing))
            if revoke_document:
                steps.append(("revoke (delete)", smoke.revoke))
        else:
            steps = [
                ("health", smoke.health),
                ("upload + publish", smoke.upload),
                ("ask + citation open", smoke.answer),
                ("internal API", smoke.internal),
                ("keep", smoke.kept) if keep else ("revoke (delete)", smoke.revoke),
            ]
        ok = True
        try:
            for name, step in steps:
                try:
                    print(f"PASS {name}: {step()}")
                except (SmokeFailure, httpx.HTTPError, KeyError) as exc:
                    print(f"FAIL {name}: {type(exc).__name__}: {exc}")
                    ok = False
                    break
        finally:
            smoke.cleanup()
    print("smoke test passed" if ok else "smoke test FAILED")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--keep", action="store_true", help="don't delete the document; print its ID")
    mode.add_argument("--check-document", metavar="ID", help="check that a kept document still answers")
    mode.add_argument("--revoke-document", metavar="ID", help="delete a kept document")
    args = parser.parse_args()
    ok = run(
        args.base_url,
        dict(os.environ),
        keep=args.keep,
        check_document=args.check_document,
        revoke_document=args.revoke_document,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

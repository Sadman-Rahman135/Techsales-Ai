"""Deployment rehearsal (blueprint §16; docs/deployment.md §5): run the release images the way they'll run when
deployed, then prove it. Everything stays on this machine; only the models are the offline substitutes.

    backend/.venv/Scripts/python scripts/rehearse_deploy.py                   # build, start, set up, verify
    backend/.venv/Scripts/python scripts/rehearse_deploy.py --rollback-drill  # then: app + index rollback
    backend/.venv/Scripts/python scripts/rehearse_deploy.py --down            # stop it, delete its volumes

Secrets are generated once into data/rehearsal.env (git-ignored), never printed and never put on a command
line. Each check prints PASS or FAIL; the script exits 1 if any failed. Stack:
infra/deploy/rehearsal/compose.yaml (HTTPS edge on 127.0.0.1:8443).
"""

import argparse
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infra" / "deploy" / "rehearsal" / "compose.yaml"
ENV_FILE = ROOT / "data" / "rehearsal.env"
CA_FILE = ROOT / "data" / "rehearsal-root.crt"
CREDENTIALS = (
    ROOT / "data" / "rehearsal-credentials.json"
)  # seeded demo passwords (git-ignored)
MANAGER, ADMIN = "priya.manager@techcare.example", "morgan.admin@techcare.example"
EDGE = "https://localhost:8443"
SECRETS = (
    "POSTGRES_PASSWORD",
    "APP_DB_PASSWORD",
    "WORKER_DB_PASSWORD",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "AUTH_JWT_SECRET",
    "INTERNAL_SERVICE_TOKEN",
    "CUSTOMER_LOOKUP_SECRET",  # ADR-029: order verification's own key
)
# The EICAR antivirus test string, in two halves so this file never matches a signature itself.
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$" + rb"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

results: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name))
    print(
        f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}",
        flush=True,
    )


def compose(*args: str, capture: bool = True, env: dict[str, str] | None = None) -> str:
    command = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE),
        "--env-file",
        str(ENV_FILE),
        *args,
    ]
    merged = {**os.environ, **(env or {})}  # shell variables override the env file
    done = subprocess.run(
        command, capture_output=capture, text=True, env=merged, check=False
    )
    if done.returncode != 0:
        raise SystemExit(
            f"docker compose {' '.join(args[:3])} failed:\n{(done.stderr or '')[-2000:]}"
        )
    return done.stdout or ""


def write_env() -> None:
    if ENV_FILE.exists():
        return
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{name}={secrets.token_urlsafe(32 if 'S3_ACCESS' not in name else 12)}"
        for name in SECRETS
    ]
    ENV_FILE.write_text(
        "# Deployment rehearsal secrets (generated, git-ignored)\n"
        + "\n".join(lines)
        + "\n"
    )


def wait_healthy(service: str, timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = compose("ps", service, "--format", "{{.Status}}").strip()
        if "(healthy)" in status:
            return
        time.sleep(3)
    raise SystemExit(f"{service} did not become healthy: {status!r}")


def run_in_api(*command: str, extra_env: dict[str, str] | None = None) -> str:
    # `-e NAME` passes the value from this process's environment: secrets never appear on a command line.
    flags = [part for name in extra_env or {} for part in ("-e", name)]
    return compose("run", "--rm", "--no-deps", *flags, "api", *command, env=extra_env)


def login_env() -> dict[str, str]:
    passwords = json.loads(CREDENTIALS.read_text())
    return {
        "SMOKE_MANAGER_EMAIL": MANAGER,
        "SMOKE_MANAGER_PASSWORD": passwords[MANAGER],
        "SMOKE_ADMIN_EMAIL": ADMIN,
        "SMOKE_ADMIN_PASSWORD": passwords[ADMIN],
    }


def smoke(*flags: str, env: dict[str, str] | None = None) -> tuple[bool, str]:
    """The post-deploy smoke test, run inside the app network (so the voice endpoint is reachable)."""
    extra = {**(env or {}), **login_env()}
    names = [part for name in login_env() for part in ("-e", name)]
    command = [
        "run",
        "--rm",
        "--no-deps",
        *names,
        "api",
        "python",
        "-m",
        "app.cli.smoke",
    ]
    full = [
        "docker", "compose", "-f", str(COMPOSE), "--env-file", str(ENV_FILE),
        *command, "--base-url", "http://api:8000", *flags,
    ]  # fmt: skip
    done = subprocess.run(
        full, capture_output=True, text=True, check=False, env={**os.environ, **extra}
    )
    return done.returncode == 0, done.stdout


def up() -> dict[str, str]:
    write_env()
    print("building the release images (compose profile app) …", flush=True)
    subprocess.run(
        ["docker", "compose", "--profile", "app", "build", "migrate", "web"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    print("starting the rehearsal stack …", flush=True)
    compose("up", "-d")
    wait_healthy("api")
    wait_healthy("web")
    compose("cp", "caddy:/data/caddy/pki/authorities/local/root.crt", str(CA_FILE))
    print(
        run_in_api("python", "-m", "app.cli.storage", "--ensure-bucket").strip(),
        flush=True,
    )
    seeded = run_in_api("python", "-m", "app.cli.seed")
    passwords = dict(re.findall(r"^\s+(\S+@\S+)\t(\S+)$", seeded, re.MULTILINE))
    if not passwords:
        raise SystemExit(
            "seed printed no passwords: the rehearsal database was seeded before; run --down first"
        )
    CREDENTIALS.write_text(json.dumps(passwords))
    return passwords


def verify() -> None:
    env = dict(
        line.split("=", 1) for line in ENV_FILE.read_text().splitlines() if "=" in line
    )

    # 1. The containers are up and only the HTTPS edge is published.
    containers = [
        json.loads(line)
        for line in compose("ps", "--format", "json").splitlines()
        if line.strip()
    ]
    open_ports = sorted(
        {
            f"{p['URL']}:{p['PublishedPort']}"
            for c in containers
            for p in c.get("Publishers") or []
            if p["PublishedPort"]
        }
    )
    check(
        open_ports == ["127.0.0.1:8443"],
        "only the HTTPS edge is published",
        f"published: {open_ports}",
    )

    # 2. The same settings pass the staging guards once a real model provider is configured (§19).
    accepted = run_in_api(
        "python",
        "-c",
        "from app.config import get_settings; s = get_settings(); print(s.app_env.value, s.storage_backend.value)",
        extra_env={
            "APP_ENV": "staging",
            "LLM_PROVIDER": "openai",
            "EMBEDDING_PROVIDER": "openai",
            "LLM_API_KEY": secrets.token_urlsafe(
                24
            ),  # never used: the check only builds the settings
        },
    )
    check(
        accepted.strip() == "staging s3",
        "the settings pass the staging guards (TLS, S3, ClamAV, secrets)",
    )

    # 3. Every component is healthy over TLS (Chroma and S3 behind TLS, Postgres TLS-only).
    health = json.loads(run_in_api("python", "-c", HEALTH_SCRIPT))
    failing = [c["name"] for c in health["checks"] if not c["ok"]]
    check(
        health["status"] == "ok",
        "readiness: postgres, chroma (TLS), storage (S3/TLS), worker, queue",
        str(failing),
    )
    plain = run_in_api(
        "python", "-c", PLAIN_SCRIPT, extra_env={"PGPASSWORD": env["APP_DB_PASSWORD"]}
    )
    check(
        "refused" in plain, "a connection without TLS is refused", plain.strip()[:120]
    )

    # 4. The HTTPS edge: HSTS, safe headers, secure cookies, cross-site writes refused.
    context = ssl.create_default_context(cafile=str(CA_FILE))
    with httpx.Client(base_url=EDGE, verify=context, timeout=30) as client:
        page = client.get("/chat")
        check(
            page.status_code == 200
            and "max-age" in page.headers.get("strict-transport-security", ""),
            "HTTPS edge serves the chat with HSTS",
        )
        check(
            page.headers.get("x-frame-options") == "SAMEORIGIN"
            and "x-powered-by" not in page.headers,
            "safe headers on the edge",
        )
        refused = client.post(
            "/api/chat/stream",
            json={"question": "hi"},
            headers={"Origin": "https://evil.example"},
        )
        check(
            refused.status_code == 403,
            "a cross-site POST to the BFF is refused (CSRF)",
            str(refused.status_code),
        )
        asked = client.post(
            "/api/chat/stream", json={"question": "Hello"}, headers={"Origin": EDGE}
        )
        cookie = asked.headers.get("set-cookie", "")
        check(
            asked.status_code == 200
            and "kai_guest=" in cookie
            and "Secure" in cookie
            and "HttpOnly" in cookie
            and "SameSite=lax" in cookie.replace("SameSite=Lax", "SameSite=lax"),
            "the guest cookie is Secure, HttpOnly, SameSite=Lax",
        )

    # 5. ClamAV (not the built-in test scanner) blocks malware, and it's audited.
    blocked = run_in_api("python", "-c", EICAR_SCRIPT, extra_env=login_env())
    signature = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "knowledgeai",
        "-d",
        "knowledgeai",
        "-tAc",
        SIGNATURE_SQL,
    ).strip()
    # ClamAV names the EICAR file "Eicar-Test-Signature" too, so the proof that clamd (not the built-in test
    # scanner) did the scan is clamd's own log line for the stream the API sent.
    clamd_log = [
        line
        for line in compose("logs", "clamav").splitlines()
        if "instream(" in line and "FOUND" in line
    ]
    check(
        blocked.strip() == "422 FILE_MALWARE_DETECTED"
        and signature == "Eicar-Test-Signature"
        and bool(clamd_log),
        "ClamAV blocks a test virus before storage; the audit log names it",
        f"{blocked.strip()}; audit: {signature}; clamd: {clamd_log[-1].split('->')[-1].strip() if clamd_log else '-'}",
    )

    # 6. The post-deploy smoke test, from inside the app network (so the voice endpoint is reachable).
    ok, output = smoke()
    for line in output.splitlines():
        if line.startswith(("PASS", "FAIL")):
            print("  smoke " + line, flush=True)
    check(
        ok,
        "smoke test: health, upload, ask, citation open, publish, revoke, internal API",
    )

    # 7. Deletion reaches object storage: the smoke document's original is still in S3 (revoked, kept for the
    # ADR-019 grace period); a retention run with no grace period purges it from the bucket.
    document = re.search(r"published document (\S+)", output)
    if document:
        count = f"from app.services.storage import get_storage; print(sum('{document[1]}' in o.key for o in get_storage().list('')))"
        before = run_in_api("python", "-c", count).strip()
        run_in_api(
            "python",
            "-m",
            "app.ingestion.worker",
            "--retention",
            extra_env={"DELETED_DOCUMENT_PURGE_DAYS": "0"},
        )
        after = run_in_api("python", "-c", count).strip()
        check(
            before == "1" and after == "0",
            "a deleted document's original is purged from S3",
            f"objects {before} → {after}",
        )
    else:
        check(
            False,
            "a deleted document's original is purged from S3",
            "no document ID in the smoke output",
        )

    # 8. After real traffic: every connection the API and the worker hold uses TLS, under separate logins.
    tls = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "knowledgeai",
        "-d",
        "knowledgeai",
        "-tAc",
        TLS_SQL,
    )
    rows = [line.split("|") for line in tls.split() if "|" in line]
    check(
        bool(rows)
        and all(ssl_on == "t" for _, _, ssl_on in rows)
        and {user for user, _, _ in rows} >= {"knowledgeai_app", "knowledgeai_worker"},
        "every API and worker connection to Postgres uses TLS, as separate logins",
        ", ".join(f"{u}/{a}" for u, a, _ in rows),
    )


HEALTH_SCRIPT = (
    "import httpx; print(httpx.get('http://api:8000/api/v1/health', timeout=30).text)"
)
TLS_SQL = (
    "SELECT a.usename, a.application_name, s.ssl FROM pg_stat_activity a JOIN pg_stat_ssl s USING (pid) "
    "WHERE a.usename LIKE 'knowledgeai_%' GROUP BY 1, 2, 3"
)
PLAIN_SCRIPT = """
import os, psycopg
try:
    psycopg.connect(host='postgres', dbname='knowledgeai', user='knowledgeai_app',
                    password=os.environ['PGPASSWORD'], sslmode='disable', connect_timeout=5).close()
    print('accepted without TLS')
except psycopg.OperationalError as exc:
    print('refused:', str(exc).splitlines()[0])
"""
EICAR_SCRIPT = (
    "import os, httpx\n"
    "c = httpx.Client(base_url='http://api:8000/api/v1', timeout=30)\n"
    "t = c.post('/auth/login', json={'email': os.environ['SMOKE_MANAGER_EMAIL'],"
    " 'password': os.environ['SMOKE_MANAGER_PASSWORD']}).json()['access_token']\n"
    f"data = {EICAR!r}\n"
    "r = c.post('/documents', headers={'Authorization': 'Bearer ' + t},"
    " files={'file': ('eicar.txt', data, 'text/plain')}, data={'audience': 'public'})\n"
    "print(r.status_code, r.json()['error']['code'])\n"
)
SIGNATURE_SQL = (
    "SELECT details->>'signature' FROM audit_events WHERE action = 'document.upload.quarantined' "
    "ORDER BY created_at DESC LIMIT 1"
)


def redeploy(env: dict[str, str]) -> None:
    compose("up", "-d", "api", "worker", "web", env=env)
    wait_healthy("api")
    wait_healthy("web")


def rollback_drill() -> None:
    """docs/deployment.md §7, rehearsed: roll the app back to the previous image tag and the retrieval index
    back to the previous Chroma collection, with one kept document answering at every step."""
    ok, out = smoke("--keep")
    kept = re.search(r"KEPT document (\S+)", out)
    check(
        ok and kept is not None,
        "a document is published and answering before the drill",
    )
    if kept is None:
        return
    document = kept[1]

    # App release rollback: the running release becomes the "previous" tag and the stack is redeployed on it.
    for image in ("knowledgeai-backend", "knowledgeai-web"):
        subprocess.run(
            ["docker", "tag", f"{image}:local", f"{image}:rehearsal-previous"],
            check=True,
        )
    previous = {"KNOWLEDGEAI_VERSION": "rehearsal-previous"}
    redeploy(previous)
    images = compose(
        "ps", "api", "worker", "web", "--format", "{{.Image}}", env=previous
    ).split()
    ok, _ = smoke("--check-document", document, env=previous)
    check(
        ok
        and len(images) == 3
        and all(i.endswith(":rehearsal-previous") for i in images),
        "app rollback: api, worker and web run the previous image tag; the document still answers",
        ", ".join(images),
    )

    # Index rollback: build the next collection beside the current one, switch to it, then switch back.
    following = {**previous, "CHROMA_COLLECTION": "knowledge_chunks_next"}
    built = compose(
        "run",
        "--rm",
        "--no-deps",
        "api",
        "python",
        "-m",
        "app.ingestion.rebuild",
        env=following,
    )
    redeploy(following)
    served_next, _ = smoke("--check-document", document, env=following)
    redeploy(previous)  # the rollback itself: the previous collection was never touched
    served_back, _ = smoke("--check-document", document, env=previous)
    summary = built.strip().splitlines()[-1][:100] if built.strip() else ""
    check(
        served_next and served_back,
        "index rollback: rebuilt into a new collection, served from it, switched back; answers on both",
        summary,
    )

    redeploy({})  # back to the current release and collection
    ok, _ = smoke("--revoke-document", document)
    check(ok, "the drill's document is deleted again")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--down", action="store_true", help="stop the rehearsal and delete its volumes"
    )
    parser.add_argument(
        "--rollback-drill", action="store_true", help="rehearse app and index rollback"
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows consoles default to cp1252
    if args.down:
        compose("down", "--volumes", capture=False)
        return
    if args.rollback_drill:
        rollback_drill()
    else:
        up()
        verify()
    failed = [name for ok, name in results if not ok]
    print(
        f"\n{len(results) - len(failed)} of {len(results)} checks passed"
        + (f"; failed: {failed}" if failed else "")
    )
    print(
        f"Edge: {EDGE} (trust {CA_FILE.relative_to(ROOT)} to open it without a warning)"
    )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

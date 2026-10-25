"""Health of every component (blueprint §5.1, §11.3 "health checks for API, DB, Chroma, worker and queue").

The API answering at all is the API check. `worker` and `queue` read the heartbeat rows the ingestion
workers write (app/ingestion/heartbeat.py). Details are deliberately coarse (exception type, or a fixed
sentence with a number) so the endpoint never echoes hosts or credentials.
"""

import tempfile
import time
from collections.abc import Callable
from typing import Annotated, Any, Literal

import httpx
import psycopg
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.config import Settings, StorageBackend, get_settings

router = APIRouter(prefix="/api/v1", tags=["health"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
TIMEOUT_SECONDS = 2.0
# One shared client: creating an httpx client costs ~200 ms (TLS context setup) vs ~2 ms per reused request.
_http = httpx.Client(timeout=TIMEOUT_SECONDS)


class ComponentCheck(BaseModel):
    name: str
    ok: bool
    latency_ms: int
    detail: str | None = None


class HealthReport(BaseModel):
    status: Literal["ok", "degraded"]
    app_env: str
    checks: list[ComponentCheck]


class Unhealthy(Exception):
    """A component answered but is not healthy; the message is safe to show (no hosts, no values)."""


def _connect(settings: Settings) -> psycopg.Connection[tuple[Any, ...]]:
    # SQLAlchemy URL "postgresql+psycopg://…" → libpq URL "postgresql://…"
    conninfo = str(settings.database_url).replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(conninfo, connect_timeout=int(TIMEOUT_SECONDS))


def check_postgres(settings: Settings) -> None:
    with _connect(settings) as conn:
        conn.execute("SELECT 1")


_FRESHEST_BEAT = """
SELECT extract(epoch FROM now() - max(beat_at))::float8,
       count(*) FILTER (WHERE beat_at > now() - make_interval(secs => %s))
FROM worker_heartbeats
"""
_OLDEST_WAIT = """
SELECT coalesce(max(queue_depth), 0), extract(epoch FROM now() - min(oldest_queued_at))::float8
FROM worker_heartbeats WHERE beat_at > now() - make_interval(secs => %s)
"""


def check_worker(settings: Settings) -> None:
    with _connect(settings) as conn:
        age, alive = conn.execute(_FRESHEST_BEAT, (settings.worker_stale_seconds,)).fetchone() or (None, 0)
    if not alive:
        raise Unhealthy(
            "No worker running." if age is None else f"No worker heartbeat for {round(float(age))} s."
        )


def check_queue(settings: Settings) -> None:
    with _connect(settings) as conn:
        depth, wait = conn.execute(_OLDEST_WAIT, (settings.worker_stale_seconds,)).fetchone() or (0, None)
    if wait is not None and float(wait) > settings.queue_max_wait_seconds:
        raise Unhealthy(f"{depth} job(s) waiting; the oldest for {round(float(wait))} s.")


def check_chroma(settings: Settings) -> None:
    scheme = "https" if settings.chroma_ssl else "http"
    url = f"{scheme}://{settings.chroma_host}:{settings.chroma_port}/api/v2/heartbeat"
    _http.get(url).raise_for_status()


def check_storage(settings: Settings) -> None:
    if settings.storage_backend is StorageBackend.LOCAL:
        with tempfile.NamedTemporaryFile(dir=settings.storage_local_dir):
            pass
    else:  # S3: one list request proves the bucket answers and the credentials may read it
        from app.services.storage import get_storage

        next(iter(get_storage().list("healthcheck/")), None)


CHECKS: dict[str, Callable[[Settings], None]] = {
    "postgres": check_postgres,
    "chroma": check_chroma,
    "storage": check_storage,
    "worker": check_worker,
    "queue": check_queue,
}


def run_check(name: str, check: Callable[[Settings], None], settings: Settings) -> ComponentCheck:
    started = time.perf_counter()
    try:
        check(settings)
    except Unhealthy as exc:
        return ComponentCheck(name=name, ok=False, latency_ms=_ms(started), detail=str(exc))
    except Exception as exc:
        return ComponentCheck(name=name, ok=False, latency_ms=_ms(started), detail=type(exc).__name__)
    return ComponentCheck(name=name, ok=True, latency_ms=_ms(started))


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


class Liveness(BaseModel):
    status: Literal["ok"]


@router.get("/health/live")
def live() -> Liveness:
    """The process serves HTTP (container liveness, §15 M7). Touches nothing else, so a database or worker
    outage never restarts a healthy API; `/health` below is the readiness check."""
    return Liveness(status="ok")


@router.get("/health")
def health(settings: SettingsDep, response: Response) -> HealthReport:
    checks = [run_check(name, check, settings) for name, check in CHECKS.items()]
    healthy = all(c.ok for c in checks)
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthReport(status="ok" if healthy else "degraded", app_env=settings.app_env.value, checks=checks)

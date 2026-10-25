"""Request counts per minute, route template and status (blueprint §13.1: error rate, rate-limited and
permission-denied requests; ADR-021).

Each API process counts in memory and adds its counts to `request_metrics` every METRICS_FLUSH_SECONDS, so
the table holds the sum over all processes and survives restarts; the worker's alert rules and
`GET /admin/metrics` read it. Only route *templates* are recorded (`/api/v1/documents/{document_id}`),
never IDs or query strings.
"""

import logging
import threading
from collections import defaultdict
from datetime import datetime

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert

from app.core.time import utcnow
from app.db.models import RequestMetric
from app.db.session import session_factory

log = logging.getLogger(__name__)

Key = tuple[datetime, str, str, int]  # (minute, method, route template, status)


class RequestCounter:
    def __init__(self) -> None:
        self._counts: defaultdict[Key, list[int]] = defaultdict(lambda: [0, 0])
        self._lock = threading.Lock()

    def record(
        self, method: str, route: str, status: int, elapsed_ms: int, now: datetime | None = None
    ) -> None:
        minute = (now or utcnow()).replace(second=0, microsecond=0)
        with self._lock:
            entry = self._counts[(minute, method, route, status)]
            entry[0] += 1
            entry[1] += max(0, elapsed_ms)

    def drain(self) -> dict[Key, list[int]]:
        with self._lock:
            counts, self._counts = self._counts, defaultdict(lambda: [0, 0])
        return dict(counts)


counter = RequestCounter()


def flush(engine: Engine, source: RequestCounter | None = None) -> int:
    """Add the drained counts to the table (upsert: concurrent processes add, never overwrite)."""
    counts = (source or counter).drain()
    if not counts:
        return 0
    rows = [
        {"bucket_start": k[0], "method": k[1], "route": k[2], "status": k[3], "count": v[0], "total_ms": v[1]}
        for k, v in counts.items()
    ]
    statement = insert(RequestMetric).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[
            RequestMetric.bucket_start,
            RequestMetric.method,
            RequestMetric.route,
            RequestMetric.status,
        ],
        set_={
            "count": RequestMetric.count + statement.excluded.count,
            "total_ms": RequestMetric.total_ms + statement.excluded.total_ms,
        },
    )
    with session_factory(engine)() as session, session.begin():
        session.execute(statement)
    return len(rows)


class Flusher:
    """Background thread of one API process: flush on an interval and once more at shutdown."""

    def __init__(self, engine: Engine, interval: float) -> None:
        self._engine, self._interval = engine, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="request-metrics", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)
        self._safe_flush()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self._safe_flush()

    def _safe_flush(self) -> None:
        try:
            flush(self._engine)
        except Exception:  # metrics must never take the API down; the counts are lost for this interval
            log.warning("could not write request metrics; will retry next interval")

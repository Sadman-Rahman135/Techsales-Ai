"""Worker heartbeats (blueprint §11.3 "health checks for … worker and queue").

Each worker process upserts one `worker_heartbeats` row every WORKER_HEARTBEAT_SECONDS. The worker reads the
queue across tenants, so it also records how deep the queue is and how long the oldest claimable job has
waited; `GET /health` turns those into the `worker` and `queue` checks. A clean shutdown deletes the row; a
crashed worker's row simply goes stale.
"""

import os
import secrets
import socket
from datetime import datetime, timedelta

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.core.time import utcnow
from app.db.models import IngestionJob, JobState, WorkerHeartbeat
from app.db.session import session_factory

STALE_ROWS_AFTER = timedelta(days=1)


def new_worker_id() -> str:
    # A random suffix, not a timestamp: long digit runs look like phone numbers to the log filter.
    return f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"


def beat(engine: Engine, worker_id: str, started_at: datetime, jobs_processed: int) -> None:
    now = utcnow()
    with session_factory(engine)() as session, session.begin():
        depth, oldest = session.execute(
            select(func.count(), func.min(IngestionJob.run_after)).where(
                IngestionJob.state == JobState.QUEUED.value, IngestionJob.run_after <= now
            )
        ).one()
        values = {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": started_at,
            "beat_at": now,
            "jobs_processed": jobs_processed,
            "queue_depth": depth,
            "oldest_queued_at": oldest,
        }
        session.execute(
            insert(WorkerHeartbeat)
            .values(worker_id=worker_id, **values)
            .on_conflict_do_update(index_elements=[WorkerHeartbeat.worker_id], set_=values)
        )
        # Rows of workers that died long ago would otherwise pile up.
        session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.beat_at < now - STALE_ROWS_AFTER))


def remove(engine: Engine, worker_id: str) -> None:
    with session_factory(engine)() as session, session.begin():
        session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id))

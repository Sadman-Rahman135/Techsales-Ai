"""Database sessions. Every tenant-scoped transaction sets `app.organization_id`, which the row-level security
policies read (ADR-011). Without it, tenant tables return no rows: the policies fail closed."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings


def build_engine(settings: Settings, url: str | None = None) -> Engine:
    """§11.3: bounded connect and statement time; §11.2: SQL parameters (questions, emails) never appear in
    error messages or logs."""
    return create_engine(
        url or str(settings.database_url),
        pool_pre_ping=True,
        pool_timeout=settings.db_connect_timeout_seconds * 2,
        hide_parameters=True,
        connect_args={
            "connect_timeout": settings.db_connect_timeout_seconds,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}"
            " -c idle_in_transaction_session_timeout=60000",
            "application_name": settings.db_application_name,
        },
    )


@lru_cache
def get_engine() -> Engine:
    return build_engine(get_settings())


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def set_tenant(session: Session, organization_id: uuid.UUID) -> None:
    """Scope the current transaction to one tenant. `is_local=true`: the setting ends with the transaction."""
    session.execute(
        text("SELECT set_config('app.organization_id', :org, true)"), {"org": str(organization_id)}
    )


@contextmanager
def tenant_session(organization_id: uuid.UUID, engine: Engine | None = None) -> Iterator[Session]:
    """One transaction for one tenant: commit on success, roll back on error."""
    factory = session_factory(engine or get_engine())
    with factory() as session, session.begin():
        set_tenant(session, organization_id)
        yield session

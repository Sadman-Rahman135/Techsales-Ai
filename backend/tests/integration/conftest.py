"""Integration fixtures: a migrated `knowledgeai_test` database and a throwaway Chroma collection.

Tests run as the non-owner app role, so row-level security applies exactly as in the API. Each test runs
inside a transaction that is rolled back, so tests never see each other's rows.
"""

import argparse
import hashlib
import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.permissions import Principal
from app.db import models as m
from app.db.session import build_engine, set_tenant
from app.ingestion.embeddings import FakeEmbedder
from app.ingestion.worker import WorkerContext
from app.integrations.chroma import ChunkMetadata, ChunkRecord, KnowledgeIndex
from app.services.storage import LocalStorage

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DB = "knowledgeai_test"


def _test_url(url: str) -> str:
    return make_url(url).set(database=TEST_DB).render_as_string(hide_password=False)


def alembic_config() -> Config:
    """Alembic pointed at the test database, migrating as the schema owner."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.cmd_opts = argparse.Namespace(x=[f"db_url={_test_url(get_settings().migration_url)}"])
    return config


def run_alembic(revision: str, direction: str = "upgrade") -> None:
    getattr(command, direction)(alembic_config(), revision)


@pytest.fixture(scope="session")
def app_engine() -> Iterator[Engine]:
    run_alembic("base", "downgrade")
    run_alembic("head")
    # The same engine settings as the API (timeouts, hidden parameters), pointed at the test database.
    engine = build_engine(get_settings(), _test_url(str(get_settings().database_url)))
    yield engine
    engine.dispose()


@pytest.fixture
def db(app_engine: Engine) -> Iterator[Session]:
    """App-role session inside an outer transaction that is always rolled back."""
    with app_engine.connect() as connection:
        outer = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()
            outer.rollback()


@pytest.fixture
def index() -> Iterator[KnowledgeIndex]:
    settings = get_settings()
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    name = f"test_{uuid.uuid4().hex}"
    yield KnowledgeIndex(client, dimension=8, embedding_model="test-embedding", collection_name=name)
    client.delete_collection(name)


# ---------- factories ----------


def sha256() -> str:
    return hashlib.sha256(secrets.token_bytes(16)).hexdigest()


class Factory:
    def __init__(self, session: Session) -> None:
        self.s = session

    def org(self, slug: str | None = None) -> m.Organization:
        org = m.Organization(name="TechCare", slug=slug or f"org-{secrets.token_hex(4)}")
        self.s.add(org)
        self.s.flush()
        return org

    def user(self, org: m.Organization, role: m.Role) -> tuple[m.User, Principal]:
        user = m.User(email=f"{secrets.token_hex(4)}@techcare.example", password_hash="argon2-test-hash")
        self.s.add(user)
        self.s.flush()
        self.s.add(m.Membership(organization_id=org.id, user_id=user.id, role=role.value))
        self.s.flush()
        return user, Principal(organization_id=org.id, user_id=user.id, role=role)

    def document(
        self, org: m.Organization, audience: m.Audience = m.Audience.PUBLIC, **kw: Any
    ) -> m.Document:
        set_tenant(self.s, org.id)
        doc = m.Document(
            organization_id=org.id, title=kw.pop("title", "Return policy"), audience=audience.value, **kw
        )
        self.s.add(doc)
        self.s.flush()
        return doc

    def version(
        self,
        doc: m.Document,
        number: int = 1,
        status: m.VersionStatus = m.VersionStatus.READY,
        chunks: int = 0,
        **kw: Any,
    ) -> m.DocumentVersion:
        set_tenant(self.s, doc.organization_id)
        version = m.DocumentVersion(
            organization_id=doc.organization_id,
            document_id=doc.id,
            version_number=number,
            checksum=kw.pop("checksum", sha256()),
            storage_key=kw.pop("storage_key", f"{doc.id}/{number}"),
            original_filename="policy.pdf",
            mime_type="application/pdf",
            size_bytes=1024,
            status=status.value,
            expected_chunk_count=chunks,
            indexed_chunk_count=chunks,
            published_at=kw.pop("published_at", None),
            **kw,
        )
        self.s.add(version)
        self.s.flush()
        for i in range(chunks):
            self.s.add(
                m.DocumentChunk(
                    organization_id=doc.organization_id,
                    version_id=version.id,
                    chunk_index=i,
                    chroma_id=f"{doc.organization_id}:{version.id}:{i}",
                    page_start=i + 1,
                    page_end=i + 1,
                    text_checksum=sha256(),
                )
            )
        self.s.flush()
        return version


@pytest.fixture
def make(db: Session) -> Factory:
    return Factory(db)


def embed(seed: int, dimension: int = 8) -> list[float]:
    """Deterministic fake embedding: unit vector with most weight on one axis."""
    vector = [0.01] * dimension
    vector[seed % dimension] = 1.0
    return vector


def index_version(
    index: KnowledgeIndex, doc: m.Document, version: m.DocumentVersion, texts: list[str]
) -> None:
    index.upsert_version(
        doc.organization_id,
        version.id,
        [
            ChunkRecord(
                chunk_index=i,
                text=text,
                embedding=embed(i),
                metadata=ChunkMetadata(
                    organization_id=doc.organization_id,
                    document_id=doc.id,
                    document_version_id=version.id,
                    title=doc.title,
                    source_type="pdf",
                    page_start=i + 1,
                    page_end=i + 1,
                    audience=m.Audience(doc.audience),
                ),
            )
            for i, text in enumerate(texts)
        ],
    )


# ---------- committed data, for code that opens its own sessions (worker, API) ----------


@dataclass
class Tenant:
    org: m.Organization
    admin: Principal
    manager: Principal
    employee: Principal
    customer: Principal
    emails: dict[m.Role, str]


def make_tenant(engine: Engine, password_hash: str = "argon2-test-hash") -> Tenant:
    with Session(engine, expire_on_commit=False) as session, session.begin():
        org = m.Organization(name="TechCare", slug=f"org-{secrets.token_hex(4)}")
        session.add(org)
        session.flush()
        principals: dict[m.Role, Principal] = {}
        emails: dict[m.Role, str] = {}
        for role in m.Role:
            email = f"{role.value}-{secrets.token_hex(4)}@techcare.example"
            user = m.User(email=email, display_name=role.value.title(), password_hash=password_hash)
            session.add(user)
            session.flush()
            session.add(m.Membership(organization_id=org.id, user_id=user.id, role=role.value))
            principals[role] = Principal(organization_id=org.id, user_id=user.id, role=role)
            emails[role] = email
    return Tenant(
        org,
        principals[m.Role.ADMIN],
        principals[m.Role.MANAGER],
        principals[m.Role.EMPLOYEE],
        principals[m.Role.CUSTOMER],
        emails,
    )


@pytest.fixture
def tenant(app_engine: Engine) -> Tenant:
    return make_tenant(app_engine)


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "uploads")


@pytest.fixture
def idle_queue(app_engine: Engine) -> None:
    """The worker claims across tenants: park jobs that earlier tests left unfinished, so none is claimed."""
    with Session(app_engine) as session, session.begin():
        session.execute(
            update(m.IngestionJob)
            .where(m.IngestionJob.state.not_in([s.value for s in m.FINISHED_JOB_STATES]))
            .values(state=m.JobState.CANCELLED.value, locked_until=None)
        )


@pytest.fixture
def worker(
    app_engine: Engine, index: KnowledgeIndex, storage: LocalStorage, idle_queue: None
) -> WorkerContext:
    settings = get_settings().model_copy(
        update={"worker_retry_base_seconds": 1, "worker_lease_seconds": 60, "orphan_grace_seconds": 60}
    )
    return WorkerContext(app_engine, index, storage, FakeEmbedder("test-embedding", 8), settings)


# ---------- question answering: the demo corpus, ingested and published once per test module ----------


@dataclass
class RagEnv:
    tenant: Tenant
    ctx: WorkerContext
    documents: dict[str, m.Document]


@pytest.fixture(scope="module")
def rag_env(app_engine: Engine, tmp_path_factory: pytest.TempPathFactory) -> Iterator[RagEnv]:
    """A tenant with the fictional TechCare corpus published, in its own 384-dim collection (8 dims would make
    the fake embedder's ranking meaningless). Built through the real path: upload → worker → publish."""
    from app.demo.corpus import load_corpus

    settings = get_settings()
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    embedder = FakeEmbedder("test-embedding", 384)
    name = f"test_rag_{uuid.uuid4().hex}"
    index = KnowledgeIndex(client, dimension=384, embedding_model=embedder.model, collection_name=name)
    storage = LocalStorage(tmp_path_factory.mktemp("rag-uploads"))
    with Session(app_engine) as session, session.begin():  # see idle_queue: the loader runs the worker
        session.execute(
            update(m.IngestionJob)
            .where(m.IngestionJob.state.not_in([s.value for s in m.FINISHED_JOB_STATES]))
            .values(state=m.JobState.CANCELLED.value, locked_until=None)
        )
    tenant = make_tenant(app_engine)
    ctx = WorkerContext(app_engine, index, storage, embedder, settings)
    documents = load_corpus(ctx, tenant.manager)
    yield RagEnv(tenant, ctx, documents)
    client.delete_collection(name)


@pytest.fixture(scope="module")
def golden_env(app_engine: Engine, tmp_path_factory: pytest.TempPathFactory) -> Iterator[RagEnv]:
    """The 21-document golden-v1 corpus (app/demo/golden_corpus.py), published through the real ingestion
    path, with the price-match revision, in a fresh tenant and its own collection."""
    from app.demo.corpus import load_corpus
    from app.demo.golden_corpus import DOCUMENTS as GOLDEN

    settings = get_settings()
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    embedder = FakeEmbedder("test-embedding", 384)
    name = f"test_golden_{uuid.uuid4().hex}"
    index = KnowledgeIndex(client, dimension=384, embedding_model=embedder.model, collection_name=name)
    storage = LocalStorage(tmp_path_factory.mktemp("golden-uploads"))
    with Session(app_engine) as session, session.begin():  # see idle_queue: the loader runs the worker
        session.execute(
            update(m.IngestionJob)
            .where(m.IngestionJob.state.not_in([s.value for s in m.FINISHED_JOB_STATES]))
            .values(state=m.JobState.CANCELLED.value, locked_until=None)
        )
    tenant = make_tenant(app_engine)
    ctx = WorkerContext(app_engine, index, storage, embedder, settings)
    documents = load_corpus(ctx, tenant.manager, documents=GOLDEN)
    yield RagEnv(tenant, ctx, documents)
    client.delete_collection(name)

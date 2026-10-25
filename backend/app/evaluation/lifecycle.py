"""G6 document-lifecycle scenarios (docs/evaluation.md §1 `lifecycle`, §2 G6): after publish, draft upload or
delete, answers may only cite the document's *active* version. 3 formats by 4 transitions = 12 scenarios.

Each scenario uses a run-unique product name, so questions reach only its own document, and every scenario
document is soft-deleted at the end (the retention job purges it later), so nothing leaks into later runs.
"""

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, select

from app.config import Settings
from app.core.permissions import Principal
from app.db.models import Audience, Document
from app.db.session import tenant_session
from app.demo.builders import docx_bytes, pdf_bytes
from app.demo.corpus import _wait_for
from app.evaluation.gates import count_gate, load_gates
from app.evaluation.runner import Personas
from app.ingestion.embeddings import Embedder
from app.ingestion.validation import validate_upload
from app.ingestion.worker import WorkerContext
from app.integrations.chroma import KnowledgeIndex
from app.rag import service
from app.rag.graph import RAGContext
from app.rag.llm import ChatModel
from app.services import documents as docs
from app.services.versions import publish_version, soft_delete_document


def _build(fmt: str, title: str, lines: list[str]) -> tuple[str, bytes]:
    if fmt == "pdf":
        return f"{title}.pdf", pdf_bytes([[("h1", title), *[("p", line) for line in lines]]])
    if fmt == "docx":
        return f"{title}.docx", docx_bytes([("title", title), *[("p", line) for line in lines]])
    return f"{title}.txt", ("# " + title + "\n" + "\n".join(lines) + "\n").encode()


def run_lifecycle(
    worker: WorkerContext,
    manager: Principal,
    admin: Principal,
    personas: Personas,
    run_id: str,
    *,
    engine: Engine,
    index: KnowledgeIndex,
    embedder: Embedder,
    llm: ChatModel,
    settings: Settings,
) -> dict[str, Any]:
    created: list[uuid.UUID] = []
    failing: list[dict[str, Any]] = []
    scenarios = 0
    tag = uuid.uuid4().hex[:5]

    def upload(fmt: str, title: str, lines: list[str], document_id: uuid.UUID | None = None) -> Any:
        name, data = _build(fmt, title, lines)
        validated = validate_upload(name, None, data, worker.settings)
        with tenant_session(manager.organization_id, engine) as session:
            result = docs.create_upload(
                session,
                worker.storage,
                worker.settings,
                manager,
                validated,
                document_id=document_id,
                title=title,
                audience=Audience.PUBLIC,
                allow_duplicate=True,
            )
        _wait_for(worker, result.job.id, 60)
        if document_id is None:
            created.append(result.document.id)
        return result

    def publish(document_id: uuid.UUID, version_id: uuid.UUID) -> None:
        with tenant_session(manager.organization_id, engine) as session:
            publish_version(session, index, manager, document_id, version_id)

    def ask(scenario: str, question: str, document_id: uuid.UUID) -> None:
        nonlocal scenarios
        scenarios += 1
        ctx = RAGContext(
            principal=personas.principal("customer"),
            engine=engine,
            index=index,
            embedder=embedder,
            llm=llm,
            settings=settings,
            request_id=f"req-{run_id}-{scenario}",
            trace_id=f"trace-{run_id}-{scenario}",
            session_ref=uuid.uuid4().hex * 2,
            caller_role="evaluation",
        )
        result = service.ask(ctx, question)
        with tenant_session(manager.organization_id, engine) as session:
            document = session.get(Document, document_id)
            active = document.active_version_id if document and document.deleted_at is None else None
        stale = [
            str(c.version_id)
            for c in result.citations
            if c.document_id == document_id and c.version_id != active
        ]
        if stale:
            failing.append(
                {"case_id": scenario, "request_id": result.request_id, "problems": [f"cited {stale}"]}
            )

    def replace(fmt: str, name: str) -> None:
        v1 = upload(fmt, name, [f"The {name} warranty lasts 2 years."])
        publish(v1.document.id, v1.version.id)
        v2 = upload(fmt, name, [f"The {name} warranty lasts 5 years."], v1.document.id)
        publish(v1.document.id, v2.version.id)
        ask(f"L-{fmt}-replace", f"How long does the {name} warranty last?", v1.document.id)

    def draft(fmt: str, name: str) -> None:
        v1 = upload(fmt, name, [f"The {name} ships in a blue box."])
        publish(v1.document.id, v1.version.id)
        upload(
            fmt,
            name,
            [f"The {name} ships in a blue box.", f"The {name} charger is sold separately."],
            v1.document.id,
        )
        ask(
            f"L-{fmt}-draft", f"Is the {name} charger sold separately?", v1.document.id
        )  # only the draft says so

    def removed(fmt: str, name: str) -> None:
        v1 = upload(
            fmt, name, [f"The {name} remote uses two AA batteries.", f"The {name} has a matte finish."]
        )
        publish(v1.document.id, v1.version.id)
        v2 = upload(fmt, name, [f"The {name} has a matte finish."], v1.document.id)
        publish(v1.document.id, v2.version.id)
        ask(f"L-{fmt}-removed", f"Which batteries does the {name} remote use?", v1.document.id)

    def deleted(fmt: str, name: str) -> None:
        v1 = upload(fmt, name, [f"The {name} is cleaned with a dry cloth."])
        publish(v1.document.id, v1.version.id)
        with tenant_session(manager.organization_id, engine) as session:
            soft_delete_document(session, admin, v1.document.id)
        ask(f"L-{fmt}-deleted", f"How is the {name} cleaned?", v1.document.id)

    transitions: dict[str, Callable[[str, str], None]] = {
        "replace": replace,
        "draft": draft,
        "removed": removed,
        "deleted": deleted,
    }
    try:
        for fmt in ("pdf", "docx", "txt"):
            for kind, scenario in transitions.items():
                scenario(fmt, f"Zephyr{kind.capitalize()}{fmt.upper()}{tag}")
    finally:
        with tenant_session(manager.organization_id, engine) as session:
            for document_id in created:
                found = session.scalar(
                    select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
                )
                if found is not None:
                    soft_delete_document(session, admin, document_id)
    return count_gate(
        "G6", "inactive_version_citation_count", len(failing), scenarios, failing, load_gates()["gates"]["G6"]
    )

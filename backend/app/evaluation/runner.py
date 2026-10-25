"""Run a labelled question set through the real graph and score the gates (docs/evaluation.md §2).

Usage (from backend/, against the configured stack):

    python -m app.evaluation.runner --dataset ../evaluation/golden_questions/golden-v1.yaml --corpus golden \\
        --setup --load-corpus --suites core,access,lifecycle --out ../evaluation/reports/latest.json

Every number is reported with its numerator, denominator and a 95% interval, never as a bare percentage.
Retrieval is scored from the persisted trace (`answers.retrieved_chunk_ids`), so the gates measure exactly
what the graph saw.

- G1 recall@k: answerable cases with an expected document (and page, and version when given) in the top k.
- G3 abstention: cases that must say "Not in our documents" but didn't, or that leaked a forbidden string.
- G4 citation validity: citations pointing at a retrieved chunk of a published version the persona may see.
- G5 access control (`--suites access`, app/evaluation/probes.py) and G6 lifecycle
  (app/evaluation/lifecycle.py).
- G9 latency: p50/p95 of the full answer and of retrieval, recorded with the configuration (no target yet).
"""

import argparse
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine, select

from app.config import Settings, get_settings
from app.core.permissions import Principal
from app.core.time import utcnow
from app.db.models import (
    Answer,
    Document,
    DocumentChunk,
    DocumentVersion,
    Membership,
    Organization,
    Role,
    User,
)
from app.db.session import get_engine, session_factory, tenant_session
from app.evaluation.gates import (
    apply_regression,
    count_gate,
    load_gates,
    ratio_gate,
    sha256_of,
    wilson,
    write_baseline,
)
from app.ingestion.embeddings import Embedder, get_embedder
from app.integrations.chroma import KnowledgeIndex, get_knowledge_index
from app.rag import service
from app.rag.graph import RAGContext
from app.rag.llm import ChatModel, get_chat_model
from app.rag.rerank import get_reranker

__all__ = ["Case", "CaseResult", "Personas", "evaluate", "load_dataset", "run_case", "score", "wilson"]


@dataclass(frozen=True, slots=True)
class Source:
    title: str
    page: int | None = None
    version: int | None = None


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    persona: str  # customer (anonymous) | employee | signed_in_customer
    question: str
    expect: str = "answered"  # answered | insufficient_evidence | direct | any
    sources: tuple[Source, ...] = ()
    # Each fact is one string, or several accepted phrasings of it ("79 dollar" | "$79", golden-v2).
    facts: tuple[str | tuple[str, ...], ...] = ()
    forbid: tuple[str, ...] = ()
    category: str = "positive"
    conversation: str | None = None
    normalized: bool = False  # dataset `scoring: normalized`: case, hyphens and spaces don't matter


@dataclass(slots=True)
class CaseResult:
    case: Case
    answer_status: str
    answer: str
    cited: list[tuple[str, int, int, int]]  # (title, page_start, page_end, version number)
    retrieved_top_k: list[tuple[str, int, int, int]]
    request_id: str
    trace_id: str
    latency_ms: int | None = None
    retrieval_ms: int | None = None
    invalid_citations: int = 0
    quotes: list[str] = field(default_factory=list)  # the cited passages, for the G2 audit sheet
    passages: list[str] = field(default_factory=list)  # full text of each cited chunk (audit only, fictional)
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


def load_dataset(path: Path) -> tuple[str, int, list[Case]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = []
    for item in raw["cases"]:
        listed = item.get("sources") or ([item["source"]] if item.get("source") else [])
        sources = tuple(
            Source(s["title"], s.get("page"), s.get("version", item.get("version"))) for s in listed
        )
        cases.append(
            Case(
                id=item["id"],
                persona=item["persona"],
                question=item["question"],
                expect=item.get("expect", "answered"),
                sources=sources,
                facts=tuple(f if isinstance(f, str) else tuple(f) for f in item.get("facts", [])),
                forbid=tuple(item.get("forbid", [])),
                category=item.get("category", "positive"),
                conversation=item.get("conversation"),
                normalized=raw.get("scoring") == "normalized",
            )
        )
    return raw["dataset"], int(raw.get("k", 5)), cases


@dataclass(frozen=True, slots=True)
class Personas:
    organization_id: uuid.UUID
    employee_user_id: uuid.UUID
    customer_user_id: uuid.UUID | None = None

    def principal(self, persona: str) -> Principal:
        if persona == "employee":
            return Principal(self.organization_id, self.employee_user_id, Role.EMPLOYEE)
        if persona == "signed_in_customer" and self.customer_user_id:
            return Principal(self.organization_id, self.customer_user_id, Role.CUSTOMER)
        return Principal(self.organization_id)  # anonymous public customer


Located = tuple[str, int, int, int, str, str]  # title, page_start, page_end, version number, status, audience


def locate(engine: Engine, org: uuid.UUID, chroma_ids: list[str]) -> dict[str, Located]:
    """chroma id → where it lives (title, pages, version number, version status, document audience)."""
    if not chroma_ids:
        return {}
    with tenant_session(org, engine) as session:
        rows = session.execute(
            select(
                DocumentChunk.chroma_id,
                Document.title,
                DocumentChunk.page_start,
                DocumentChunk.page_end,
                DocumentVersion.version_number,
                DocumentVersion.status,
                Document.audience,
            )
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentChunk.chroma_id.in_(chroma_ids))
        ).all()
    return {
        r.chroma_id: (r.title, r.page_start, r.page_end, r.version_number, r.status, r.audience) for r in rows
    }


def run_case(
    case: Case,
    k: int,
    personas: Personas,
    *,
    engine: Engine,
    index: KnowledgeIndex,
    embedder: Embedder,
    llm: ChatModel,
    settings: Settings,
    run_id: str,
    conversation_id: uuid.UUID | None = None,
) -> tuple[CaseResult, uuid.UUID]:
    principal = personas.principal(case.persona)
    thread = case.conversation or case.id
    ctx = RAGContext(
        principal=principal,
        engine=engine,
        index=index,
        embedder=embedder,
        llm=llm,
        settings=settings,
        request_id=f"req-{run_id}-{case.id}",
        trace_id=f"trace-{run_id}-{case.id}",
        conversation_id=conversation_id,
        session_ref=None
        if principal.user_id
        else hashlib.sha256(f"eval:{run_id}:{thread}".encode()).hexdigest(),
        caller_role="evaluation",  # analytics leaves evaluation traffic out (§13)
        reranker=get_reranker(),  # RERANKER_PROVIDER from the environment, as in the API
    )
    result = service.ask(ctx, case.question)
    with tenant_session(personas.organization_id, engine) as session:
        row = session.execute(
            select(Answer.retrieved_chunk_ids, Answer.latency_ms, Answer.timings).where(
                Answer.id == result.answer_id
            )
        ).one()
    retrieved_ids: list[str] = list(row.retrieved_chunk_ids or [])
    where = locate(
        engine, personas.organization_id, [*retrieved_ids, *(c.chunk_id for c in result.citations)]
    )
    top_k = [where[i][:4] for i in retrieved_ids[:k] if i in where]
    cited = [
        (c.title, c.page_start, c.page_end, where[c.chunk_id][3] if c.chunk_id in where else 0)
        for c in result.citations
    ]
    outcome = CaseResult(
        case,
        result.answer_status.value,
        result.answer,
        cited,
        top_k,
        result.request_id,
        result.trace_id,
        latency_ms=row.latency_ms,
        retrieval_ms=(row.timings or {}).get("retrieve_documents"),
        quotes=[c.quote for c in result.citations],
        passages=_passages(index, personas.organization_id, result.citations),
    )

    audiences = {a.value for a in principal.allowed_audiences}
    for citation in result.citations:
        found = where.get(citation.chunk_id)
        if (
            citation.chunk_id not in retrieved_ids
            or found is None
            or found[4] != "published"
            or found[5] not in audiences
        ):
            outcome.invalid_citations += 1
    if outcome.invalid_citations:
        outcome.problems.append(f"{outcome.invalid_citations} invalid citation(s)")

    text = result.answer.lower()
    quotes = " ".join(c.quote.lower() for c in result.citations)
    leaked = [f for f in case.forbid if f.lower() in text or f.lower() in quotes]
    if leaked:
        outcome.problems.append(f"leaked {leaked}")

    status = result.answer_status.value
    if case.expect == "answered":
        if status != "answered":
            outcome.problems.append(f"expected an answer, got {status}")
        elif case.sources and not any(matches(c, case) for c in cited):
            outcome.problems.append(f"did not cite {[(s.title, s.page) for s in case.sources]}")
        missing = [f for f in case.facts if not has_fact(result.answer, f, case.normalized)]
        if status == "answered" and missing:
            outcome.problems.append(f"answer lacks {missing}")
    elif case.expect != "any" and status != case.expect:
        outcome.problems.append(f"expected {case.expect}, got {status}")
    return outcome, result.conversation_id


def has_fact(answer: str, fact: str | tuple[str, ...], normalized: bool = False) -> bool:
    """The answer states the fact (any of its accepted phrasings). golden-v1 matches case-insensitively;
    `scoring: normalized` datasets also ignore hyphens and spacing ("90-day" is "90 day", "8:45 am" is
    "8:45am")."""
    phrasings = (fact,) if isinstance(fact, str) else fact
    if not normalized:
        return any(p.lower() in answer.lower() for p in phrasings)
    return any(_squash(p) in _squash(answer) for p in phrasings)


def _squash(text: str) -> str:
    return re.sub(r"[\s-]+", "", text.lower())


def _passages(index: KnowledgeIndex, org: uuid.UUID, citations: list[Any]) -> list[str]:
    """The indexed text of each cited chunk, read as a manager (the diagnostics read is managers-only)."""
    reader = Principal(org, uuid.UUID(int=0), Role.MANAGER)
    texts: dict[str, str] = {}
    for version_id in {c.version_id for c in citations}:
        texts.update(index.version_chunks(reader, version_id))
    return [texts.get(c.chunk_id, "") for c in citations]


def matches(location: tuple[str, int, int, int], case: Case) -> bool:
    title, start, end, version = location
    return any(
        title == s.title
        and (s.page is None or start <= s.page <= end)
        and (s.version is None or version == s.version)
        for s in case.sources
    )


def _ids(results: list[CaseResult]) -> list[dict[str, Any]]:
    return [
        {"case_id": r.case.id, "request_id": r.request_id, "trace_id": r.trace_id, "problems": r.problems}
        for r in results
    ]


def _pct(values: list[float], q: float) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0])
    return round(statistics.quantiles(values, n=100, method="inclusive")[round(q * 100) - 1])


def score(dataset: str, k: int, results: list[CaseResult], config: dict[str, Any]) -> dict[str, Any]:
    specs = load_gates()["gates"]
    answerable = [r for r in results if r.case.expect == "answered" and r.case.sources]
    abstaining = [r for r in results if r.case.expect == "insufficient_evidence"]
    hits = [r for r in answerable if any(matches(loc, r.case) for loc in r.retrieved_top_k)]
    abstain_failures = [r for r in abstaining if r.problems]
    citations_total = sum(len(r.cited) for r in results)
    citations_invalid = sum(r.invalid_citations for r in results)
    answered_correctly = [r for r in answerable if r.passed]

    gates = [
        ratio_gate(
            "G1",
            f"recall_at_{k}",
            len(hits),
            len(answerable),
            _ids([r for r in answerable if r not in hits]),
            specs["G1"],
        ),
        count_gate(
            "G3",
            "invented_policy_count",
            len(abstain_failures),
            len(abstaining),
            _ids(abstain_failures),
            specs["G3"],
        ),
        ratio_gate(
            "G4",
            "valid_citation_rate",
            citations_total - citations_invalid,
            citations_total,
            _ids([r for r in results if r.invalid_citations]),
            specs["G4"],
        ),
    ]
    latencies = [
        float(r.latency_ms) for r in results if r.latency_ms is not None and r.answer_status != "direct"
    ]
    retrievals = [float(r.retrieval_ms) for r in results if r.retrieval_ms is not None]
    by_category: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_category.setdefault(r.case.category, {"passed": 0, "total": 0})
        bucket["total"] += 1
        bucket["passed"] += int(r.passed)
    return {
        "run_id": config["run_id"],
        "commit": config.get("commit"),
        "dataset": dataset,
        "dataset_sha256": config.get("dataset_sha256"),
        "gated": dataset == load_gates()["dataset"],
        "started_at": config["started_at"],
        "config": {
            key: v
            for key, v in config.items()
            if key not in {"run_id", "started_at", "commit", "dataset_sha256"}
        },
        "gates": gates,
        "latency": {
            "id": "G9",
            "status": "no_target",
            "full_answer_ms": {
                "p50": _pct(latencies, 0.5),
                "p95": _pct(latencies, 0.95),
                "n": len(latencies),
            },
            "retrieval_ms": {
                "p50": _pct(retrievals, 0.5),
                "p95": _pct(retrievals, 0.95),
                "n": len(retrievals),
            },
            "note": "sequential runs, offline models; first-token latency needs a streaming client (G9)",
        },
        "by_category": by_category,
        "m3_gate": {
            "status": "pass"
            if len(answered_correctly) == len(answerable) and not abstain_failures
            else "fail",
            "answered_from_expected_source": {
                "numerator": len(answered_correctly),
                "denominator": len(answerable),
            },
            "abstained_when_required": {
                "numerator": len(abstaining) - len(abstain_failures),
                "denominator": len(abstaining),
            },
            "failing_cases": _ids([r for r in answerable if not r.passed] + abstain_failures),
        },
        "cases": [
            {
                "case_id": r.case.id,
                "category": r.case.category,
                "persona": r.case.persona,
                "question": r.case.question,
                "expect": r.case.expect,
                "answer_status": r.answer_status,
                "answer": r.answer,
                "cited": [
                    {"title": t, "page_start": s, "page_end": e, "version": v} for t, s, e, v in r.cited
                ],
                "retrieved_top_k": [
                    {"title": t, "page_start": s, "page_end": e, "version": v}
                    for t, s, e, v in r.retrieved_top_k
                ],
                "quotes": r.quotes,
                "passages": r.passages,
                "latency_ms": r.latency_ms,
                "passed": r.passed,
                "problems": r.problems,
                "request_id": r.request_id,
            }
            for r in results
        ],
    }


def run_cases(cases: list[Case], k: int, personas: Personas, run_id: str, **deps: Any) -> list[CaseResult]:
    """Cases run in file order; cases sharing `conversation` continue one conversation (follow-ups)."""
    threads: dict[str, uuid.UUID] = {}
    results = []
    for case in cases:
        previous = threads.get(case.conversation) if case.conversation else None
        result, conversation_id = run_case(case, k, personas, run_id=run_id, conversation_id=previous, **deps)
        if case.conversation:
            threads[case.conversation] = conversation_id
        results.append(result)
    return results


def evaluate(
    dataset_path: Path,
    personas: Personas,
    *,
    engine: Engine,
    index: KnowledgeIndex,
    embedder: Embedder,
    llm: ChatModel,
    settings: Settings,
) -> dict[str, Any]:
    dataset, k, cases = load_dataset(dataset_path)
    started = utcnow()
    run_id = f"eval-{started:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    results = run_cases(
        cases, k, personas, run_id, engine=engine, index=index, embedder=embedder, llm=llm, settings=settings
    )
    config = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "commit": _commit(),
        "dataset_sha256": sha256_of(dataset_path),
        "llm_model": f"{settings.llm_provider.value}:{settings.llm_model}",
        "embedding_model": f"{settings.embedding_provider.value}:{settings.embedding_model}",
        "top_k": k,
        "retrieval_candidates": settings.retrieval_candidates,
        "min_term_coverage": settings.min_term_coverage,
        "cases": len(cases),
    }
    return score(dataset, k, results, config)


def _commit() -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    out = subprocess.run([git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False)  # noqa: S603 - fixed arguments
    return out.stdout.strip() or None


# ---------- command line ----------


def _setup_organization(engine: Engine, slug: str) -> None:
    """Create an evaluation tenant with one user per role (no passwords: they never sign in)."""
    with session_factory(engine)() as session, session.begin():
        org = session.scalar(select(Organization).where(Organization.slug == slug))
        if org is None:
            org = Organization(name=f"TechCare ({slug})", slug=slug)
            session.add(org)
            session.flush()
        for role in (Role.ADMIN, Role.MANAGER, Role.EMPLOYEE, Role.CUSTOMER):
            if session.scalar(
                select(Membership).where(Membership.organization_id == org.id, Membership.role == role.value)
            ):
                continue
            user = User(email=f"{role.value}@{slug}.eval.example", auth_subject=f"eval:{slug}:{role.value}")
            session.add(user)
            session.flush()
            session.add(Membership(organization_id=org.id, user_id=user.id, role=role.value))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="write the JSON report here")
    parser.add_argument(
        "--organization", default=None, help="organization slug (default: PUBLIC_ORGANIZATION)"
    )
    parser.add_argument(
        "--corpus", choices=["m3", "golden"], default="m3", help="corpus to load with --load-corpus"
    )
    parser.add_argument("--load-corpus", action="store_true", help="upload + publish the corpus first")
    parser.add_argument(
        "--setup", action="store_true", help="create the organization and its personas if missing"
    )
    parser.add_argument("--suites", default="core", help="comma-separated: core, access (G5), lifecycle (G6)")
    parser.add_argument("--fail-on-gates", action="store_true", help="exit 1 when a gate fails (CI)")
    parser.add_argument(
        "--update-baseline", action="store_true", help="accept this run as the regression baseline"
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = get_engine()
    slug = args.organization or settings.public_organization
    if args.setup:
        _setup_organization(engine, slug)
    with session_factory(engine)() as session:
        org = session.scalar(select(Organization.id).where(Organization.slug == slug))
        if org is None:
            raise SystemExit(f"organization {slug!r} not found; run python -m app.cli.seed or pass --setup")
        members = dict(
            (row.role, row.user_id)
            for row in session.execute(
                select(Membership.role, Membership.user_id).where(Membership.organization_id == org)
            )
        )
    if Role.EMPLOYEE.value not in members or Role.MANAGER.value not in members:
        raise SystemExit(
            "the organization needs an employee and a manager (python -m app.cli.seed or --setup)"
        )

    index = get_knowledge_index()
    embedder = get_embedder()
    manager = Principal(org, members[Role.MANAGER.value], Role.MANAGER)
    from app.ingestion.worker import default_context

    ctx = default_context()
    if args.load_corpus:
        from app.demo.corpus import DOCUMENTS as M3
        from app.demo.corpus import load_corpus
        from app.demo.golden_corpus import DOCUMENTS as GOLDEN

        load_corpus(ctx, manager, documents=GOLDEN if args.corpus == "golden" else M3)

    personas = Personas(org, members[Role.EMPLOYEE.value], members.get(Role.CUSTOMER.value))
    deps: dict[str, Any] = {
        "engine": engine,
        "index": index,
        "embedder": embedder,
        "llm": get_chat_model(),
        "settings": settings,
    }
    report = evaluate(args.dataset, personas, **deps)
    suites = {s.strip() for s in args.suites.split(",") if s.strip()}
    if "access" in suites:
        from app.evaluation.probes import run_access_probes

        report["gates"].append(run_access_probes(personas, report["run_id"], **deps))
    if "lifecycle" in suites:
        from app.evaluation.lifecycle import run_lifecycle

        if Role.ADMIN.value not in members:
            raise SystemExit("the lifecycle suite needs an admin in the organization (--setup)")
        admin = Principal(org, members[Role.ADMIN.value], Role.ADMIN)
        report["gates"].append(run_lifecycle(ctx, manager, admin, personas, report["run_id"], **deps))
    if report["gated"]:
        apply_regression(report["dataset"], report["gates"], load_gates()["gates"])

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    for gate in report["gates"]:
        n = gate.get("numerator", gate["value"])
        print(
            f"{gate['id']} {gate['status']}: {n} / {gate['denominator']}"
            + (f" ({gate['regression']})" if gate.get("regression") else "")
        )
    print(f"G9 latency (no target): {report['latency']['full_answer_ms']}")
    if args.update_baseline:
        print("baseline written:", write_baseline(report))
    if args.fail_on_gates and any(g["status"] == "fail" for g in report["gates"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

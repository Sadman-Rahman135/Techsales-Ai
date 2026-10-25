"""G9 performance run (docs/evaluation.md): golden-v1 questions over HTTP, the way the chat UI asks them.

It streams `POST /api/v1/chat/ask/stream` as a guest and records, per request, the time to the first `delta`
event (what the customer perceives), the time to `final`, and the retrieval step from the answer's trace
(`answers.timings`). Two phases: N sequential requests, then M requests from C concurrent clients.

Guest traffic is recorded like any guest's, so point it at an API whose PUBLIC_ORGANIZATION is an evaluation
tenant (techcare-golden), never at the real one, and raise that instance's guest budget so the limiter isn't
what gets measured (it has its own tests). docs/operations.md §11 has the commands.

    cd backend
    .venv/Scripts/python -m app.evaluation.load --base-url http://127.0.0.1:18001 \\
        --organization techcare-golden --out ../evaluation/reports/load.json
"""

import argparse
import json
import os
import platform
import secrets
import statistics
import threading
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from app.config import REPO_ROOT, get_settings
from app.db.models import Answer, Organization
from app.db.session import get_engine, session_factory, tenant_session
from app.evaluation.runner import Case, load_dataset

DATASET = REPO_ROOT / "evaluation" / "golden_questions" / "golden-v1.yaml"


@dataclass(slots=True)
class Sample:
    phase: str
    case_id: str
    status: int  # HTTP status, or 0 when the connection failed
    outcome: str  # answered | insufficient_evidence | error | http_error
    request_id: str | None = None
    first_token_ms: int | None = None
    full_ms: int | None = None
    retrieval_ms: int | None = None
    tokens: dict[str, Any] | None = None  # answers.token_usage (§13.1)


def questions(path: Path = DATASET) -> list[Case]:
    """Single-turn questions a guest may ask: customer persona, no follow-up conversation."""
    _, _, cases = load_dataset(path)
    return [c for c in cases if c.persona == "customer" and c.conversation is None and c.expect != "direct"]


def events(lines: Iterator[str]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Server-sent events as (event, data) pairs; data lines are JSON."""
    event, data = "message", ""
    for line in lines:
        if not line:
            if data:
                yield event, json.loads(data)
            event, data = "message", ""
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data += line[5:].strip()


def ask(client: httpx.Client, base_url: str, phase: str, case: Case) -> Sample:
    headers = {"X-Session-Ref": secrets.token_urlsafe(24), "Accept": "text/event-stream"}
    sample = Sample(phase=phase, case_id=case.id, status=0, outcome="http_error")
    started = time.perf_counter()
    try:
        with client.stream(
            "POST", f"{base_url}/api/v1/chat/ask/stream", json={"question": case.question}, headers=headers
        ) as response:
            sample.status = response.status_code
            if response.status_code != 200:
                return sample
            for event, data in events(response.iter_lines()):
                elapsed = round((time.perf_counter() - started) * 1000)
                if event == "meta":
                    sample.request_id = data.get("request_id")
                elif event == "delta" and sample.first_token_ms is None:
                    sample.first_token_ms = elapsed
                elif event == "final":
                    sample.full_ms = elapsed
                    sample.outcome = str(data.get("answer_status", "error"))
                elif event == "error":
                    sample.full_ms = elapsed
                    sample.outcome = "error"
    except httpx.HTTPError:
        sample.outcome = "http_error"
    return sample


def run_sequential(client: httpx.Client, base_url: str, cases: list[Case], n: int) -> list[Sample]:
    return [ask(client, base_url, "sequential", cases[i % len(cases)]) for i in range(n)]


def run_concurrent(
    client: httpx.Client, base_url: str, cases: list[Case], n: int, concurrency: int
) -> tuple[list[Sample], float]:
    """`n` requests from `concurrency` clients at once; returns the samples and the wall time in seconds."""
    samples: list[Sample] = []
    lock = threading.Lock()
    counter = iter(range(n))

    def worker() -> None:
        while True:
            with lock:
                i = next(counter, None)
            if i is None:
                return
            sample = ask(client, base_url, "concurrent", cases[i % len(cases)])
            with lock:
                samples.append(sample)

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    started = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return samples, time.perf_counter() - started


def attach_retrieval(samples: list[Sample], organization: str) -> None:
    """Retrieval time and token usage per request, from the persisted trace (§13.2), matched by request ID."""
    engine = get_engine()
    with session_factory(engine)() as session:
        org = session.scalar(select(Organization.id).where(Organization.slug == organization))
    if org is None:
        raise SystemExit(f"organization {organization!r} not found")
    ids = [s.request_id for s in samples if s.request_id]
    with tenant_session(org, engine) as session:
        query = select(Answer.request_id, Answer.timings, Answer.token_usage).where(
            Answer.request_id.in_(ids)
        )
        traces = {row.request_id: row for row in session.execute(query)}
    for s in samples:
        trace = traces.get(s.request_id) if s.request_id else None
        node = (trace.timings or {}).get("retrieve_documents") if trace else None
        s.retrieval_ms = round(node) if isinstance(node, int | float) else None
        s.tokens = dict(trace.token_usage) if trace and trace.token_usage else None


def pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return round(statistics.quantiles(values, n=100, method="inclusive")[round(q * 100) - 1])


def summarize(samples: list[Sample], wall_seconds: float | None = None) -> dict[str, Any]:
    def stat(values: list[int]) -> dict[str, int | None]:
        return {"p50": pct(values, 0.5), "p95": pct(values, 0.95), "n": len(values)}

    ok = [s for s in samples if s.status == 200 and s.outcome != "error"]
    summary: dict[str, Any] = {
        "requests": len(samples),
        "http_status": dict(Counter(str(s.status) for s in samples)),
        "outcomes": dict(Counter(s.outcome for s in samples)),
        "errors": sum(1 for s in samples if s.status != 200 or s.outcome in {"error", "http_error"}),
        "retrieval_ms": stat([s.retrieval_ms for s in ok if s.retrieval_ms is not None]),
        "first_token_ms": stat([s.first_token_ms for s in ok if s.first_token_ms is not None]),
        "full_ms": stat([s.full_ms for s in ok if s.full_ms is not None]),
    }
    answered = [s.tokens for s in ok if s.outcome == "answered" and s.tokens]
    if answered:
        summary["tokens_per_answer"] = {
            key: round(statistics.mean(float(t.get(key) or 0) for t in answered), 6 if "cost" in key else 1)
            for key in ("embedding", "prompt", "completion", "llm_calls", "estimated_cost_usd")
        }
    if wall_seconds:
        summary["throughput_rps"] = round(len(samples) / wall_seconds, 1)
    return summary


def environment(base_url: str) -> dict[str, Any]:
    settings = get_settings()
    return {
        "base_url": base_url,
        "client_os": f"{platform.system()} {platform.release()}",
        "client_cpus": os.cpu_count(),
        "python": platform.python_version(),
        "llm_model": f"{settings.llm_provider.value}:{settings.llm_model}",
        "embedding_model": f"{settings.embedding_provider.value}:{settings.embedding_model}",
        "note": "API, worker and data services in Docker on the same machine; offline substitute models",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--organization", default="techcare-golden", help="the API's PUBLIC_ORGANIZATION")
    parser.add_argument("--sequential", type=int, default=200)
    parser.add_argument("--concurrent", type=int, default=100, help="requests in the concurrent phase")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    cases = questions()
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    with httpx.Client(timeout=60.0, limits=limits) as client:
        ask(client, args.base_url, "warmup", cases[0])  # first request loads models and connections
        sequential = run_sequential(client, args.base_url, cases, args.sequential)
        concurrent, wall = run_concurrent(client, args.base_url, cases, args.concurrent, args.concurrency)
    attach_retrieval(sequential + concurrent, args.organization)

    report: dict[str, Any] = {
        "dataset": "golden-v1",
        "questions": len(cases),
        "environment": environment(args.base_url),
        "sequential": summarize(sequential),
        "concurrent": {"concurrency": args.concurrency, **summarize(concurrent, wall)},
        "samples": [asdict(s) for s in sequential + concurrent],
    }
    for phase in ("sequential", "concurrent"):
        s = report[phase]
        print(
            f"{phase}: {s['requests']} requests, {s['errors']} errors, outcomes {s['outcomes']}; "
            f"retrieval {s['retrieval_ms']}, first token {s['first_token_ms']}, full {s['full_ms']}"
            + (f", {s['throughput_rps']} req/s" if "throughput_rps" in s else "")
        )
    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

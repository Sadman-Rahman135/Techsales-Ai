# KnowledgeAI

RAG knowledge base for **TechCare**, a fictional electronics retailer. Stack: FastAPI · LangGraph · ChromaDB · PostgreSQL · Next.js.
Status: MVP built; all §19 decisions made (ADR-026, ADR-027). Customer chat (`/chat`), employee assistant (`/assistant`),
document center (`/admin/documents`), quality and operations dashboards run on the API, graph and worker; golden-v1
gates, the load test and release images (compose profile `app`) are in. Answers come from **local models** (llama.cpp,
`LLM/`), never a paid API; pytest uses the fake models. Next: provision the VM (one VM with Compose), P1.
Code: `backend/` (FastAPI, `app/`), `frontend/` (Next.js 16.4 App Router). **Read `frontend/AGENTS.md` before writing Next.js code**: 16.4 has breaking changes, and the correct docs are in `frontend/node_modules/next/dist/docs/`.

## Where things live

- `docs/IMPLEMENTATION_PLAN.md`: source of truth (requirements, design, tests, release). `md/` holds an outdated copy; ignore it.
- `docs/product-vision.md`: personas, permission matrix, user stories US-01 to US-07 with acceptance criteria.
- `docs/scope.md`: what is in and out of the MVP, P0/P1/P2 by milestone and screen, non-goal guardrails.
- `docs/evaluation.md` + `evaluation/gates.yaml`: acceptance gates G1–G10. Change thresholds only in `gates.yaml`. Report counts + confidence intervals, never a bare %.
  golden-v1 (`evaluation/golden_questions/`) is **frozen** (sha256 file): never edit it, make golden-v2; tune only on `evaluation/exploration/`.
- `docs/architecture.md`: ADRs and open §19 decisions. Record new decisions here.
- `docs/data-model.md`: tables, delete behaviour, RLS, Chroma metadata, version lifecycle (code: `backend/app/db/models.py`).
- `docs/contracts/`: the frozen voice API contract (`knowledge-answer.v1`), its client rules for Project 2, and the
  generated `openapi.json` (people's version: `docs/api-contract.md`). `docs/threat-model.md`: threats → controls → tests.
- `docs/repository.md`: where everything lives (checked by `tests/unit/test_repository.py`; update it when you move things).
- `docs/deployment.md`: how to deploy (provider-neutral), release and rollback; `infra/deploy/` holds the rehearsal,
  DB roles and S3 policies.
- `docs/operations.md`: runbook: health, backup/restore, index rebuild, retention, shutdown, scanning, alerts, incidents,
  containers and the load test.
- `LLM/`: the local models (`models.json` pins revisions + SHA-256; the `.gguf` files in `LLM/models/` are git-ignored).
  Served by the compose services `llm` and `embedder`; `compose.gpu.yaml` adds the NVIDIA GPU.
- `docs/dependency-versions.md`: pinned versions. `backend/constraints.txt` is the generated full lock.
- `design-system/MASTER.md`: UI tokens, components, checklist. Read `design-system/pages/<page>.md` for overrides and `design-system/content-guide.md` for all user-facing copy.

## Rules

- **Evidence before ticking:** tick a plan checkbox only when the work has automated tests, a manual test, and a screenshot/demo in `docs/demos/`. A UI existing is not completion.
- **Standalone (ADR-001):** Project 1 never imports, calls, or requires Project 2 (the voice agent).
- **Authorization before retrieval:** tenant (`organization_id`) and audience filters are applied server-side *before* any context reaches the LLM. Never trust a caller-supplied role or audience. Query Chroma only through the `Principal`-filtered wrapper (ADR-004); never add an unfiltered query helper.
- **Database access:** tenant data is read and written only inside `tenant_session(org_id)` / `set_tenant()`; the app role
  (`DATABASE_URL`) is subject to RLS and sees nothing without a tenant. Migrations run as the owner (`MIGRATION_DATABASE_URL`).
  Schema changes: edit `app/db/models.py`, `alembic revision --autogenerate`, **review the file** (autogenerate misses RLS,
  triggers, and circular FKs), round-trip it, then add tests. Never edit a migration that has been applied anywhere shared.
- **Architecture decisions** live in `docs/architecture.md` (ADR-001…031): Postgres is the truth and Chroma is rebuildable, active versions are resolved from Postgres, the browser talks only to the Next.js BFF, answers stream over SSE where only the `final` event is authoritative, ingestion jobs run in a Postgres-backed worker. Don't contradict an ADR without adding a new one.
- **No invented answers:** when evidence is inadequate, return `insufficient_evidence` with no citations. Citations may only reference retrieved, authorized chunks.
- **Dependencies:** use the versions in `docs/dependency-versions.md`; don't upgrade silently. Never hand-edit `backend/constraints.txt`. Regenerate it and re-run the tests.
- **No paid AI APIs:** the owner spends $0 on model credits. Use the local models (`LLM_PROVIDER=local`); don't add a
  paid provider, key or hosted model without asking. A model change goes in `LLM/models.json` plus an ADR.
- **Data and secrets:** fictional demo data only. Never commit `.env` or real keys; never paste a real `.env` into chat.
- **Scope:** respect the non-goals in `docs/scope.md` (no internet search, no LLM-executed SQL, no policy creation without approval, no orders in Chroma).
- **Priorities:** label work P0 (MVP), P1 (production), or P2 (stretch). Don't build P1/P2 inside a P0 task.

## UI rules

- Use semantic tokens (`bg-surface`, `text-fg-muted`, `text-accent`); no raw hex in components.
- Icons come from `lucide-react` (2px stroke, 16/20/24). No emoji as icons.
- Every answer shows its evidence state (icon + text), never color alone.
- Charts follow `design-system/MASTER.md` §10: validated `--mark-*` tokens, accent and status colors never share a chart, a table view for every chart. Run the dataviz palette validator before adding colors.
- Before calling a screen done, run the checklist at the end of `design-system/MASTER.md` in light **and** dark mode.

## Commands

Run from the repo root unless noted. On Windows the venv uses `.venv/Scripts/`; elsewhere use `.venv/bin/`.

| Task | Command |
| --- | --- |
| Start / stop data services | `docker compose up -d` / `docker compose down` (volumes kept) |
| Install backend | `cd backend; python -m venv .venv; .venv/Scripts/python -m pip install -c constraints.txt -e ".[dev]"` |
| Migrate DB | `cd backend; .venv/Scripts/alembic upgrade head` (check drift: `alembic check`) |
| Run API (port 8000) | `cd backend; .venv/Scripts/fastapi dev` |
| Run ingestion worker | `cd backend; .venv/Scripts/python -m app.ingestion.worker` (`--once`, `--reconcile`) |
| Seed demo users | `cd backend; .venv/Scripts/python -m app.cli.seed` (passwords → `data/dev-credentials.txt`, git-ignored) |
| Seed demo orders | `cd backend; .venv/Scripts/python -m app.cli.seed_orders` (after `seed`; fictional catalogue, customers, orders and repairs; `DEMO_ORDERS`, `DEMO_REPAIRS` in `app/demo/orders.py`; re-run on an older database to add repairs) |
| Seed test customers | `cd backend; .venv/Scripts/python -m app.cli.seed_test_customers` (after `seed_orders`; Maya, Omar, Lena: 5 orders each, 2 delivered in October 2026, 3 due after 15 November; logins added to `data/dev-credentials.txt`) |
| Demo documents | `backend/.venv/Scripts/python scripts/make_demo_docs.py` (→ `data/demo-docs/`) |
| M3 evaluation | `cd backend; .venv/Scripts/python -m app.evaluation.runner --dataset ../evaluation/datasets/m3-handpicked.yaml --load-corpus` |
| golden-v2 (local models) | same as golden-v1 with `golden-v2.yaml` and `--suites core`, after `source ../data/local-models.env` (a git-ignored file with the local model settings; see `.env.example`) |
| golden-v1 gates | `cd backend; .venv/Scripts/python -m app.evaluation.runner --dataset ../evaluation/golden_questions/golden-v1.yaml --corpus golden --organization techcare-golden --setup --load-corpus --suites core,access,lifecycle --out ../evaluation/reports/latest.json` (`--fail-on-gates`; `--update-baseline` only for an accepted run) |
| G2 audit sheet | `cd backend; .venv/Scripts/python -m app.evaluation.audit --report <report.json> --sample 30 --out ../evaluation/audits/<name>.md` |
| API contracts check | `cd backend; .venv/Scripts/python -m app.cli.contracts` (voice schema + `openapi.json`; `--write` after an intended change) |
| Whole app in containers | `docker compose --profile app up -d --build` (web 127.0.0.1:13000, api 127.0.0.1:18000; images: `backend/Dockerfile`, `frontend/Dockerfile`) |
| Load test (G9) | `cd backend; .venv/Scripts/python -m app.evaluation.load` against a load API container (`docs/operations.md` §11) |
| Regenerate the backend lock | `backend/.venv/Scripts/python scripts/lock_backend.py` (needs the backend image built; `--check` verifies; after adding a dependency: `--from-local`, rebuild the image, run again) |
| Deployment rehearsal | `backend/.venv/Scripts/python scripts/rehearse_deploy.py` (`--rollback-drill`, `--down`; `docs/deployment.md` §5) |
| Post-deploy smoke test | `cd backend; .venv/Scripts/python -m app.cli.smoke --base-url <api>` (SMOKE_* credentials in the environment) |
| Local models | `backend/.venv/Scripts/python scripts/download_models.py` (`--check`), then `docker compose --profile llm up -d` (GPU: `-f compose.yaml -f compose.gpu.yaml`) |
| Red-team the local model | `cd backend; RED_TEAM_LLM_URL=http://127.0.0.1:8080/v1 .venv/Scripts/python -m pytest tests/integration/test_adversarial.py` |
| Rebuild vector index | `cd backend; .venv/Scripts/python -m app.ingestion.rebuild` (`--drop-existing` in development) |
| Backend checks | `cd backend; .venv/Scripts/ruff check .; .venv/Scripts/ruff format --check .; .venv/Scripts/mypy app tests` |
| Backend tests | `cd backend; .venv/Scripts/python -m pytest` (integration tests need the compose stack; skip them with `-m "not integration"`) |
| Backup + restore check | `backend/.venv/Scripts/python scripts/db_backup.py backup --verify` (dumps to `data/backups/`) |
| Retention once | `cd backend; .venv/Scripts/python -m app.ingestion.worker --retention` (also runs in the worker loop) |
| Install / run web (port 3000) | `cd frontend; npm ci; npm run dev` |
| Frontend checks | `cd frontend; npm run lint; npm run typecheck; npm test; npm run build` (`npm test`: Node's runner on `lib/*.test.ts`) |
| All hooks | `backend/.venv/Scripts/pre-commit run --all-files` |

Local gotchas: the Postgres init script (app role + `knowledgeai_test` DB) runs only on a fresh volume. On an older volume run
`MSYS_NO_PATHCONV=1 docker compose exec -T postgres sh /docker-entrypoint-initdb.d/01-roles-and-test-db.sh` once. Postgres is on host port **55432**; use `127.0.0.1`, not `localhost` (Windows adds ~2 s per connection);
reuse HTTP clients (a new httpx client costs ~200 ms).
Embeddings: the index refuses a different embedding model (ADR-013); the fake model's ID carries its algorithm version, so
after changing it run the rebuild command. RAG tests use the module-scoped `rag_env` fixture (demo corpus, 384-dim collection).
Ingestion: the API only queues jobs; without the worker running, uploads stay "Waiting". Integration tests park other tests'
unfinished jobs (`idle_queue` fixture) because the worker claims across tenants. Never paste literal `\uXXXX` text through
the Write tool for code: it is decoded into invisible characters (write escapes from a script instead).

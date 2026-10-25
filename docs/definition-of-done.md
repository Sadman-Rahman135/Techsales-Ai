# Definition of Done audit (blueprint §18, 2026-10-20)

Each §18 item with its evidence, or the reason it's open. An item is ticked in the plan only with automated
tests, a manual run and a demo (CLAUDE.md). Result: **16 of 18 met**; the two open items need things outside
this machine (the first GitHub CI run, and a staging environment). The project is therefore **not "finished"**
in the §18 sense yet.

## Functional

| Item | Met | Evidence |
| --- | --- | --- |
| Upload, validate, ingest, retrieve, answer and cite work end-to-end | ✓ | E2E demos 1–4 (`demos/12-evaluation.md` §12.5); the same path in the containers (`demos/15-01…`, `15-02…`) and in the deployment rehearsal's smoke test (`demos/16-rehearsal-transcript.txt`); `test_rag_graph`, `test_documents_api` |
| A document replacement can't expose old or unpublished versions | ✓ | G6 0 / 12 lifecycle scenarios (replace, draft, removed, deleted × PDF/DOCX/TXT); drafts visible to managers only (`test_api_spec::test_preview_respects_audience_and_draft_rules`); demo 3 (v1 → v2 citations) |
| Unsupported questions get an explicit, evidence-based fallback | ✓ | G3 0 / 36; "Not in our documents" with no citations (demo 2, video scenario 2); `test_rag_graph::test_unknown_topics_rewrite_once_then_abstain` |
| Customer/employee access boundaries are tested | ✓ | G5 0 / 96 probes; `test_api_spec::test_role_matrix`; RLS tests incl. the worker's login; demo 4 and video scenarios 3 and 7 |
| Admin can monitor jobs and failed queries | ✓ | Upload stepper and job API; Quality (unanswered questions, feedback, ingestion failures) and Operations (alerts, errors, pipeline) pages (`demos/10-ui.md`, `13-observability.md`, video scenarios 8–9) |
| The voice endpoint works without the voice project | ✓ | Frozen contract checked in CI (`test_api_support::test_the_voice_contract_matches_its_frozen_schema`); external test client (`demos/9-api-spec.md`); smoke test's internal-API step in the rehearsal |

## Engineering

| Item | Met | Evidence / why open |
| --- | --- | --- |
| CI passes lint, type, unit, integration, security and evaluation checks | **Open** | `.github/workflows/ci.yml` runs all of them (plus image builds); every command passes locally (lint, mypy, 499 backend tests, contracts, gitleaks via pre-commit, pip-audit, npm audit, frontend build). It has never run on GitHub: that needs your push |
| No usable secrets in code, git history or screenshots | ✓ | gitleaks (pre-commit) passes on every tracked file; no commits exist yet, and CI scans the full history from the first push; secrets are generated into git-ignored files (`.env`, `data/`); the evidence pages and transcripts contain no tokens (searched for JWT, provider-key and AWS-key patterns); the screenshots that could show credentials (API docs, sign-in) show none, and the UI never displays tokens (they stay in the BFF's httpOnly cookie) |
| Production/staging error logs and metrics available | **Open** | Built and shown locally: structured logs with request IDs, the metrics API, Operations, alerts (`demos/13-observability.md`), and in the rehearsal. A staging environment doesn't exist yet (the host is decided, one VM with Compose (ADR-027); deployment.md §1a and §9 say what to set up) |
| Backups/restores and vector index rebuild tested | ✓ | `test_backup` (restore check incl. RLS policies), `test_ingestion::test_rebuild_reindexes_live_versions_into_a_new_collection`, rollback drill (index switch) in the rehearsal |
| Dependency versions and migration instructions documented | ✓ | `docs/dependency-versions.md` (pins with release dates, lock policy), `scripts/lock_backend.py`; migrations: README, CLAUDE.md, deployment.md §3 and §7 (expand/contract, downgrade round-trip in every test run) |
| Demo dataset is permitted/public or completely fictional | ✓ | Every document is generated and fictional (`test_evaluation::test_the_corpus_is_fictional_varied_and_canaried`); people and orders are invented |

## Documentation and presentation

| Item | Met | Evidence |
| --- | --- | --- |
| README: purpose, screenshots, features, architecture, setup, env variables, scripts, testing, deployment | ✓ | `README.md` |
| OpenAPI documentation and sample authenticated requests | ✓ | `docs/contracts/openapi.json` (checked in CI), `docs/api-contract.md` (samples run against the containers) |
| Architecture diagram and data flow | ✓ | README diagram; `docs/architecture.md` §1–§3 (containers and trust boundaries, ingestion and question flows) |
| Evaluation report shows quality numbers, failures and limitations | ✓ | `docs/evaluation-report.md` |
| 3–5 minute demonstration video and a few realistic test scenarios | ✓ | `docs/demos/18-demo-scenarios.mp4` (3:03, captioned, no voice): nine scenarios below |
| Clear list of P1/P2 features not implemented | ✓ | README "Not built"; `docs/scope.md` §2; `docs/risk-register.md` "Still open" |

## The demonstration scenarios

Recorded on the container stack, in this order (`docs/demos/18-demo-scenarios.mp4`):

1. A guest asks how to return an opened item: answer with two citations; the source panel shows the passage, page
   and version.
2. A guest asks about something not covered (electric scooters): "Not in our documents", with a support link.
3. A guest asks an employee-only question (waiving the diagnostic fee): refused; nothing internal leaks.
4. A guest asks for an order's status: handed to support (order lookup is P1).
5. Priya (manager) signs in and uploads a new "Weekend Repair Desk" policy: the processing steps, then publishing.
6. The policy changes: she uploads and publishes version 2 (version 1 is archived); the Chunks tab shows what the
   assistant can quote.
7. In the staff assistant, the Sunday question is answered from version 2; the employee-only question is
   answered and marked *Employee only*, with "Copy for customer" disabled.
8. Quality: the unanswered questions from scenarios 2 and 3 appear as content gaps.
9. Operations: alerts, errors, time per step, tokens and cost, the document pipeline.

A shorter walkthrough (`demos/15-demo-walkthrough.mp4`, 88 s) covers the version change in more detail.

## What closes the two open items

1. Push to GitHub; the CI workflow runs every check above and the image builds. Fix anything it finds.
2. Provision the VM (deployment.md §1a; host and local models decided in ADR-026/027), deploy, run the smoke test,
   and confirm logs and metrics arrive where deployment.md §9 says.

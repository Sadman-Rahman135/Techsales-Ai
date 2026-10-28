# Evidence: §11 security, privacy, reliability (2026-10-19)

Every §11 checkbox, with what was built, how it is tested and what is still open. Decisions: ADR-019 (retention) and
ADR-020 (hardening); runbook: `docs/operations.md`. Manual run: `docs/demos/11-transcript.txt` (dev stack, no secrets
printed).

## Automated tests

`cd backend; .venv/Scripts/python -m pytest` → **423 passed**. `ruff`, `ruff format --check`, `mypy --strict` clean.
New for §11: `tests/integration/test_operations.py` (9), `tests/integration/test_backup.py` (2), `tests/unit/test_hygiene.py`
(17), TLS and network settings in `tests/unit/test_config.py` (+13), a zip-bomb test and a retry-cap test.

## §11.1 Identity and access control

| Item | Status | What and where | Tests | Manual |
| --- | --- | --- | --- | --- |
| Admin changes produce audit events | ✓ | Sign-in, upload, new version, publish, cancel, retry, delete, quarantine, purge and retention expiry write `audit_events` with actor and request ID; the table is append-only (trigger). The document page's Activity tab shows them | `test_operations::test_every_admin_change_is_audited` (exact sequence, actors, request IDs, UPDATE refused), `test_screens_api::test_activity_*` | `10-ui.md` step 17 |
| Voice integration: scoped service auth + network restriction | ✓ | Token (constant-time compare) **and** caller inside `INTERNAL_API_ALLOWED_NETWORKS` (TCP peer; forged `X-Forwarded-For` ignored); public audience only (ADR-017) | `test_operations::test_the_internal_api_only_answers_allowed_networks`, `test_config::test_internal_networks_are_parsed_and_validated`, `test_api_spec::test_user_tokens_cannot_call_the_service_api` | transcript §5 |

## §11.2 Data and model security

| Item | Status | What and where | Tests | Manual |
| --- | --- | --- | --- | --- |
| TLS/HTTPS outside development; encryption at rest | Partly | Staging/production refuse to start without `sslmode=require|verify-*`, `CHROMA_SSL=true`, an `https://` S3 endpoint; HSTS from API and BFF; secure cookies. **Open:** encryption at rest is provisioning (encrypted volumes and bucket SSE, M7) | `test_config::TestDeployedEnvironments` (TLS cases), `test_hygiene::test_deployed_environments_add_hsts_and_hide_the_docs` | transcript §2, §3 |
| Validate files; path traversal, malicious links, unsafe archives | ✓ | §7 validation: allowlist + magic bytes, filenames sanitized, storage keys can't leave the root, DOCX zip-bomb guard and macro refusal, malware scan. Links in documents never become clickable: answers render as text, and the validator rejects URLs that aren't in the cited source | `test_validation` (13 incl. `…inflate_past_the_limit…`), `test_storage::test_keys_cannot_escape_the_storage_root`, `test_scanning`, `test_rag::test_invalid_answers_are_rejected` (invented URL) | `7-ingestion.md` |
| Limit uploads/queries by user/org/IP | ✓ | Questions: 10/min per guest session, 30/min per user, 300/min all guests, 20/min per IP at the BFF. Sign-in: account lockout. Uploads: size and page limits, 5 active ingestions per organization | `test_operations::test_new_anonymous_sessions_share_one_public_budget`, `…test_repeated_wrong_passwords_lock_the_account_for_a_while`, `test_hygiene::test_failure_lock_…`, `test_chat_api::test_rate_limited_callers_get_429_with_retry_after`, `test_ingestion::test_concurrent_ingestions_per_tenant_are_limited` | transcript §4; `10-ui.md` step 8 (session and IP caps) |
| Retrieved instructions are untrusted | Open | Unchanged from §8: sources wrapped as data, validated citations. Red-team with the real model (§19) | — | — |
| SSRF if URL ingestion is enabled | Not applicable yet | No code fetches user-supplied URLs; URL ingestion is P2. ADR-020's rule stands for when it's added | — | — |
| No document bodies or raw PII in logs | ✓ | App logs IDs/codes/timings only; `RedactingFilter` on every handler (tokens, file links, `q=`, emails, phones, cards, order numbers); SQL parameters hidden | `test_hygiene` (9 leak cases, uvicorn access lines, tracebacks), `test_operations::test_slow_statements_are_cancelled_and_parameters_stay_hidden` | transcript §6 (real API log lines) |
| Retention and deletion, tested in all stores | ✓ | ADR-019: chats deleted on request or after 180 days idle; deleted documents purged after 30 days from Chroma, storage and Postgres; audit kept. Periods for real data: §19 | `test_operations::test_retention_purges_every_store` (file, vectors, rows, grace period, audit), `test_screens_api::test_owners_delete_one_conversation_or_clear_their_history` | transcript §9; `10-ui.md` steps 9-10 |
| Provider data handling and regions | Open | Needs the provider decision (§19); nothing real is uploaded | — | — |
| Restrict debugging traces; redact secrets | ✓ | Generic 500 body with a request ID only; `/docs`, `/redoc`, `/openapi.json` hidden outside development; secrets are `SecretStr`; logs scrubbed | `test_hygiene::test_deployed_environments_add_hsts_and_hide_the_docs`, `test_health::test_component_down_returns_503_without_leaking_details` | transcript §2, §6 |
| Dependency and secret scanning in CI | Built, not yet run on GitHub | `.github/workflows/ci.yml` (gitleaks on history, pip-audit, npm audit). Ran the same commands locally: one accepted, documented exception (chromadb, ADR-020); runtime npm deps clean; no secrets in files git would track | — (the workflow is the test) | transcript §12 |

## §11.3 Reliability

| Item | Status | What and where | Tests | Manual |
| --- | --- | --- | --- | --- |
| Timeouts on embedding, LLM, database, object storage | Partly | LLM 20 s, embeddings 60 s, request 30 s, **database connect 5 s and statements 15 s** (new). **Open:** S3 client timeouts come with the S3 adapter (M7; local disk today) | `test_operations::test_slow_statements_are_cancelled…`, `test_rag_graph::test_a_request_past_its_deadline…` | transcript §7 |
| Capped retries | ✓ | Jobs: 3 attempts, exponential backoff capped at 15 min; embeddings: bounded backoff honouring Retry-After; LLM: one retry; voice clients: one retry (contract) | `test_ingestion::test_transient_errors_retry_with_backoff_until_attempts_run_out`, `…test_backoff_grows_exponentially`, `test_embeddings::test_backoff_is_bounded_then_gives_up_as_retryable`, `test_rag::test_provider_retries_are_capped_at_one` | — |
| Idempotent indexing and publication | ✓ | Deterministic Chroma IDs + prune; re-running a job rewrites, never duplicates; publishing the published version is a no-op; concurrent publishes leave one | `test_ingestion::test_reprocessing_a_version_is_idempotent`, `…test_concurrent_publishes_leave_exactly_one_published_version`, `test_screens_api::test_activity_lists_every_change_with_its_actor` (repeat publish) | — |
| Postgres backups and tested restore | ✓ (local) | `scripts/db_backup.py backup --verify`: dump, restore to a scratch DB, compare revision, row counts per table and RLS policies. Managed backups are M7 | `test_backup::test_a_backup_restores_with_every_row_and_policy`, `…test_a_damaged_backup_fails_the_check` | transcript §8 (15 tables, 1017 rows) |
| Vector-index rebuild procedure | ✓ | `python -m app.ingestion.rebuild` (§8) + runbook; the Chunks tab shows drift; publish refuses drifted versions | `test_ingestion::test_rebuild_reindexes_live_versions_into_a_new_collection` | `8-rag-workflow.md` step 14; `10-ui.md` step 18 |
| Health checks for API, DB, Chroma, worker, queue | ✓ | `GET /health` adds `worker` and `queue` from worker heartbeats | `test_operations::test_a_running_worker_reports_healthy_then_stops_gracefully`, `…test_a_stuck_queue_is_reported`, `…test_heartbeats_count_the_claimable_queue`, `test_local_stack` | transcript §1, §11 (a killed worker → 503 after 60 s, recovers on restart) |
| Graceful shutdown and job recovery | ✓ | SIGINT/SIGTERM finish the current job, wake the poll at once, remove the heartbeat; a killed worker's job is requeued when its lease expires and can't overwrite the newer attempt | `test_operations::test_a_running_worker_reports_healthy_then_stops_gracefully` (stops in < 3 s from a 5 s poll), `test_ingestion::test_an_expired_attempt_cannot_overwrite_the_new_one`, `…test_expired_job_without_attempts_left_fails` | transcript §11 |

## Found and fixed while building

- **A security finding, recorded rather than hidden:** `pip-audit` reports four advisories against `chromadb 1.5.9` (the newest
  release, no fix yet), two of them code injection on the Chroma server. Mitigation and review date are in ADR-020; CI
  ignores exactly those IDs so anything new fails.
- The first log filter collapsed each record into its message; uvicorn's access formatter unpacks the arguments, so every
  access line became a logging error. The filter now scrubs arguments in place, with a test using uvicorn's formatter.
- SQLAlchemy hides SQL parameters, but Postgres repeats a rejected value in its own error text (an email, in the test).
  That is what the log filter catches.
- The worker ID ended in a Unix timestamp, which the phone-number scrubber redacted. It now ends in a random suffix.
- `test_local_stack` assumed three health checks; it now accepts that a test run has no worker.

## Not done here

- Encryption at rest, managed backups, S3 timeouts, network policy for Chroma: deployment work (M7, §16).
- Provider data handling and prompt-injection red-teaming: need the real model (§19).
- The first CI run on GitHub (you push; the workflow is ready).

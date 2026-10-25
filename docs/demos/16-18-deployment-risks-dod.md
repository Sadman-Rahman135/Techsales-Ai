# Evidence: §16 deployment, §17 risks, §18 Definition of Done (2026-10-20)

What was built, how it is tested, what was run by hand, and what is still open. Decision: ADR-025. Guides:
[deployment.md](../deployment.md), [risk-register.md](../risk-register.md),
[definition-of-done.md](../definition-of-done.md), [evaluation-report.md](../evaluation-report.md).

## Automated tests

`cd backend; .venv/Scripts/python -m pytest` → **499 passed** (0 skipped). `ruff`, `ruff format --check`,
`mypy --strict` clean; `python -m app.cli.contracts` matches (OpenAPI regenerated for the new settings); lock
`--check` matches both platforms; `pip-audit` finds nothing new.

| New test | Proves |
| --- | --- |
| `test_s3_storage::test_both_backends_keep_the_same_contract` (local + S3) | The S3 adapter behaves exactly like local storage: overwrite, list with UTC times, idempotent delete, `FileNotFoundError` |
| `test_s3_storage::test_unsafe_keys_are_refused_before_any_request` (×8) | Key validation happens before any S3 request |
| `test_s3_storage::test_the_client_has_timeouts_and_capped_retries`, `…test_an_unreachable_store_fails_fast` | §11.3 timeouts on object storage (closes that open §11 item) |
| `test_schema::test_the_worker_has_its_own_login_under_the_same_isolation` | The worker's role: no BYPASSRLS, sees nothing without a tenant, only its tenant with one, no DDL |
| `test_observability::test_model_spend_over_the_daily_budget_fires_and_resolves` | Priced answers count across tenants; the budget alert fires, resolves, and is off when unset |
| `test_hygiene::test_the_api_grants_no_cross_origin_access` | No CORS headers for a foreign origin, preflight or simple request |
| `test_smoke::test_missing_credentials_fail_before_any_request`, `…test_a_failed_step_stops_the_run_and_deletes_the_test_document` | The smoke client stops at the first failure, cleans up, never prints credentials |
| `test_repository::test_tests_cited_as_evidence_exist` (extended) | Test names cited in the risk register, deployment guide and DoD audit exist |

## Manual runs

1. **Smoke test against the container stack** (`docker compose --profile app`, run inside the app network):
   health, upload + publish, ask + citation open, internal API, revoke: all PASS.
2. **Deployment rehearsal** from clean volumes (`scripts/rehearse_deploy.py`): **12 of 12** checks
   (`16-rehearsal-transcript.txt`): only `127.0.0.1:8443` published; settings pass the staging guards; readiness
   over TLS (Chroma and S3 behind TLS); a non-TLS Postgres connection refused; HSTS and safe headers on the edge;
   a cross-site POST refused (403); guest cookie `Secure; HttpOnly; SameSite=Lax`; ClamAV blocks a test virus
   (clamd's own log line, 422, audit record); the smoke test; a deleted document's original purged from S3
   (1 → 0); every API and worker DB connection on TLS under separate logins.
3. **Rollback drill** (`--rollback-drill`): **4 of 4**: a kept document answers; api, worker and web redeployed
   on the previous image tag (still answers); index rebuilt into `knowledge_chunks_next`, served from it,
   switched back (answers on both); the document deleted.
4. **Demonstration video** `18-demo-scenarios.mp4` (3:03, captioned): nine scenarios, listed in
   definition-of-done.md.

## Found and fixed while doing this

- **No S3 adapter existed**, while deployed settings require S3: a deployed environment couldn't have started.
  Built (ADR-025).
- botocore's `max_attempts` counts retries, not attempts: the setting now maps to `total_max_attempts`
  (caught by the timeout test).
- The rehearsal containers couldn't read the CA certificate (Caddy keeps its PKI root-only): a one-shot service
  copies only the public certificate.
- The first ClamAV check compared signature names, but ClamAV and the built-in scanner both call EICAR
  "Eicar-Test-Signature": the check now requires clamd's own log line for the stream.
- Adding boto3 pulled a botocore release one day old into the Linux image: the lock script's platform check
  caught it; `--from-local` breaks the chicken-and-egg (lock first, then rebuild the image).
- Secrets were passed as `-e NAME=value` on `docker` command lines in the first rehearsal script; values now go
  through the process environment only.
- Smoke-test questions appear on the tenant's Quality page: documented as expected (deployment.md §8).

## Still open

- §16: the production domain and certificate, the managed Postgres instance, the Chroma host volume, the real
  bucket and IAM roles: they need the hosting decision (§19).
- §18: the first CI run on GitHub; staging logs and metrics.
- §17: lexical/hybrid search and reranking, an answer cache, OCR (all P1); the G2 human audit; real-model
  red-teaming.

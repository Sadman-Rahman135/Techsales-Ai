# Deploying KnowledgeAI (blueprint §16)

For whoever puts KnowledgeAI on a real host. The chosen host (ADR-027) is **one Linux VM running Docker Compose**,
with the local models on the same VM (ADR-026, no paid model API). §1a shows that setup. The rest of this guide
names what each piece must provide, so a managed platform works as well. Everything here was rehearsed locally
with the release images (§5); what still needs the real host is marked.

Related: [operations.md](operations.md) (runbook) · [threat-model.md](threat-model.md) · ADR-020 (hardening),
ADR-023 (images), ADR-025 (deployment), ADR-026 (local models), ADR-027 (§19 decisions) in
[architecture.md](architecture.md).

## 1. What to provision

| Piece | Must provide | Rehearsed locally with |
| --- | --- | --- |
| Public edge | HTTPS with a real certificate for the production domain, HTTP→HTTPS redirect, `X-Forwarded-For` set (overwritten, not appended) and `X-Forwarded-Host` | Caddy on `https://localhost:8443` |
| `web` (Next.js BFF) | 1+ containers of `knowledgeai-web:<tag>`, reachable only from the edge; reaches only `api` | same image |
| `api` | 1+ containers of `knowledgeai-backend:<tag>`, private network only, health check `GET /api/v1/health/live`, readiness `GET /api/v1/health` | same image |
| `worker` | 1+ containers of the same backend image running `python -m app.ingestion.worker`, restarted on failure, 60 s stop grace | same image |
| `migrate` | The same image running `python -m alembic upgrade head` once per release, as the schema owner | same image |
| PostgreSQL 18 | Managed, TLS required, point-in-time backups, the three roles of §3 | `postgres:18.6-alpine` with TLS-only `pg_hba.conf` |
| Vector index | Chroma 1.5.9 on a persistent volume, private network only, TLS in front | Chroma behind Caddy TLS |
| Object storage | S3 (or S3-compatible) bucket per environment, §4 | SeaweedFS behind Caddy TLS |
| Malware scanner | ClamAV `clamd` reachable on the private network, signatures updated | `clamav/clamav:1.5.4` |
| Secret store | Every secret in §6, injected as environment variables | `data/rehearsal.env` (generated, git-ignored) |
| Models | The `llm` and `embedder` services (llama.cpp, models from `LLM/models/`), private network only; no key, no spend (ADR-026) | offline substitutes |

The app refuses to start in `staging`/`production` without TLS to Postgres and Chroma, S3 storage, `clamd`,
non-placeholder secrets and a real model provider (`local` or `openai`, `app/config.py`); the rehearsal checks
that its settings pass those guards.

## 1a. The chosen setup: one VM with Docker Compose

- **Size:** 4+ vCPU, 16 GB RAM (8 GB is the floor: the two model servers hold about 3.5 GB), 40 GB disk. An
  NVIDIA GPU with 6 GB or more makes answers about 10 times faster.
- **Install:** Docker Engine with Compose, then the repository at the release tag. Get the models once:
  `python scripts/download_models.py` (2.6 GB from Hugging Face, each file checked against `LLM/models.json`).
- **Run:** `docker compose --profile app up -d` (add `-f compose.yaml -f compose.gpu.yaml` before `--profile` on a
  GPU host). The models need no key.
- **Index:** build the local-model collection once (`docker compose exec api python -m app.ingestion.rebuild`) and
  again after any embedding model change (ADR-013).
- **Speed on CPU:** about 8 tokens/s, so a typical answer takes 15–30 s. Raise the time limits on api and worker:
  `LLM_TIMEOUT_SECONDS=90` and `REQUEST_DEADLINE_SECONDS=120`. The answer streams, so people see progress. On the
  laptop GPU (RTX 4050) the p95 was 2.8 s, inside the defaults.
- **Edge and data:** put Caddy (or another proxy) in front of `web` with the domain's certificate, publish only
  80/443, and keep the compose ports on 127.0.0.1. Postgres and Chroma run as the compose services on volumes:
  schedule `scripts/db_backup.py backup --verify` nightly and copy the dumps off the VM. Originals go in the
  `uploads` volume, or an S3-compatible bucket (§4).
- **What compose doesn't give you:** managed PITR, so restore from the nightly dump (at most a day of loss), and
  a second machine. A VM failure means downtime until a restore. That is accepted for the MVP (risk register).

## 2. Domains, HTTPS, CORS and cookies

- **One public origin:** browsers talk only to the web edge (ADR-005). The API is never published; the voice
  service reaches it on the private network.
- **HTTPS:** the edge terminates TLS. The web app adds `Strict-Transport-Security` in production builds, plus
  `X-Frame-Options`, `nosniff`, a referrer policy and a permissions policy (`next.config.ts`).
- **CORS:** the API sends no CORS headers at all, so no other origin can call it from a browser
  (`test_hygiene::test_the_api_grants_no_cross_origin_access`). The BFF refuses state-changing requests whose
  `Origin` isn't its own host (CSRF; every POST/DELETE route uses `sameOrigin`).
- **Cookies:** the session (`kai_session`) and guest (`kai_guest`) cookies are `HttpOnly`, `SameSite=Lax` and
  `Secure` in production. The rehearsal verifies all three on the HTTPS edge.
- **Rehearsed:** HSTS and the safe headers on the edge; a cross-site POST refused (403); the guest cookie flags.

## 3. Database: roles, TLS, backups

Run `infra/deploy/postgres/roles.sql` once as the owner, before the first migration (passwords from the secret
store):

| Role | Used by | Can |
| --- | --- | --- |
| owner (the provider's admin role) | the `migrate` job only | DDL; never serves traffic |
| `knowledgeai_app` | `api` | read/write tenant data under row-level security (no BYPASSRLS) |
| `knowledgeai_worker` | `worker` | the app role's privileges (same RLS), its own password: rotate or cut it off alone |

- **TLS:** every `DATABASE_URL` carries `?sslmode=require` (or `verify-full` with the provider's CA). Set
  `DB_APPLICATION_NAME` per service (`knowledgeai-api`, `knowledgeai-worker`) so connections are attributable.
- **Backups:** the provider's point-in-time recovery (keep ≥ 14 days), plus the monthly restore check
  (operations.md §2, `scripts/db_backup.py verify` against a restored copy).
- **Rehearsed:** a connection without TLS is refused; every API and worker connection uses TLS under its own
  login (`pg_stat_ssl`); the worker role is held to RLS (`test_schema::test_the_worker_has_its_own_login_under_the_same_isolation`).

## 4. Object storage

- One bucket per environment, **versioning on**, **default encryption on** (SSE-S3 or KMS), public access
  blocked, `infra/deploy/s3/bucket-policy.json` (TLS only; the API and worker roles may get, put, delete and list,
  nothing else), `infra/deploy/s3/lifecycle.json` (non-current versions expire after 35 days; incomplete uploads
  after 1 day).
- On a cloud host, give the API and worker an instance/task role and leave `S3_ACCESS_KEY_ID` unset: boto3 finds
  the role's credentials. For an S3-compatible store set `S3_ENDPOINT_URL` (https) and the keys.
- **Deletion:** deleting a document revokes its links and answers at once; the retention job purges the original
  after `DELETED_DOCUMENT_PURGE_DAYS` (30), and bucket versioning keeps the last copy 35 more days, so an
  original is gone from storage at most ~65 days after deletion. Shorten both for real customer data.
- **Rehearsed:** bucket write/read/delete over TLS; a deleted document's original purged from S3 (1 → 0 objects).
  Adapter tests: `test_s3_storage` (same contract as local storage, timeouts, unreachable store fails fast).

## 5. The deployment rehearsal

```sh
backend/.venv/Scripts/python scripts/rehearse_deploy.py                  # build, start, set up, verify (≈ 3 min)
backend/.venv/Scripts/python scripts/rehearse_deploy.py --rollback-drill # §7, rehearsed
backend/.venv/Scripts/python scripts/rehearse_deploy.py --down           # remove it and its volumes
```

It runs `infra/deploy/rehearsal/compose.yaml`: the release images; Caddy issuing every certificate from one local
CA (edge, Chroma, S3, Postgres); TLS-only Postgres; SeaweedFS as S3; ClamAV; separate DB logins; only
`127.0.0.1:8443` published. It then checks: only the edge is published · the settings pass the staging guards ·
readiness of every component over TLS · plain-text DB connections refused · HSTS and safe headers · CSRF refused ·
cookie flags · ClamAV blocks a test virus and the audit log names it · the smoke test (§8) · deletion reaches S3
· every DB connection is TLS under separate logins. Latest result: 12 of 12, rollback drill 4 of 4
(`docs/demos/16-rehearsal-transcript.txt`).

What the rehearsal can't show: real certificates and DNS, the provider's managed services and IAM, real model
latency (it substitutes the fake models; the local models' latency is measured in §1a and ADR-026). Those are the
first things to check on the real host.

## 6. Secrets and configuration

All settings are environment variables (`.env.example` lists every one). Never deploy a `.env` file; inject from
the secret store. Each environment has its own values:

| Secret | Who needs it |
| --- | --- |
| `AUTH_JWT_SECRET` | api (signs sessions) |
| `INTERNAL_SERVICE_TOKEN` | api (checks it) and the voice service (sends it) |
| `CUSTOMER_LOOKUP_SECRET` (32+ characters) | api (keys the customer contact hashes, ADR-029). **Don't rotate casually:** a new value makes every stored hash unmatchable until the customers are re-imported |
| DB passwords (owner, app, worker) | migrate, api, worker respectively |
| `S3_*` keys (only without an IAM role) | api, worker |
| `LLM_API_KEY` (only with `openai`; local models need none) | api, worker |
| `ALERT_WEBHOOK_URL` | worker |

`web` needs no secret: only `API_BASE_URL`. Rotating a secret = update the store, restart the services that use it
(sessions signed with an old `AUTH_JWT_SECRET` end; people sign in again).

## 7. Releasing and rolling back

**Release** (one tag for both images, e.g. the git commit):

1. Build and push `knowledgeai-backend:<tag>` and `knowledgeai-web:<tag>` (CI builds both on every change).
2. Run the `migrate` job with `<tag>`. Migrations are **expand/contract**: a release only adds (columns,
   tables, nullable fields); removals ship one release later. So the previous release still works on the new
   schema, and rolling back the app never needs a database downgrade.
3. Deploy `api`, `worker` and `web` with `<tag>` (rolling; the worker finishes its job on SIGTERM).
4. Run the smoke test (§8). Watch Operations for 15 minutes (error rate, answer errors, ingestion).

**Roll back the app:** redeploy the previous tag for `api`, `worker` and `web`; run the smoke test. Every
migration has a working downgrade (the test suite runs `downgrade base` then `upgrade head` on every run), but
use it only for a migration that can't be left in place, and after a backup.

**Roll back the retrieval index:** a model or chunker change is built into a **new** collection
(`CHROMA_COLLECTION=…_next python -m app.ingestion.rebuild`), checked, then switched to by changing
`CHROMA_COLLECTION` on api and worker. The old collection is never touched, so rolling back is setting
`CHROMA_COLLECTION` back and restarting; delete the old one only after a release or two. If the chunker changed,
Postgres chunk rows were rewritten too: rebuild the old collection with the old release instead of only switching.

**Roll back data:** restore Postgres to a point in time (provider PITR), then rebuild the index from it
(operations.md §3); originals are in the versioned bucket.

**Rehearsed:** `--rollback-drill` keeps a document live, redeploys api, worker and web on the previous image tag
(still answers), rebuilds the index into `knowledge_chunks_next`, serves from it, switches back (answers on
both), then deletes the document.

## 8. After every deploy: the smoke test

```sh
SMOKE_MANAGER_EMAIL=… SMOKE_MANAGER_PASSWORD=… SMOKE_ADMIN_EMAIL=… SMOKE_ADMIN_PASSWORD=… \
INTERNAL_SERVICE_TOKEN=… python -m app.cli.smoke --base-url https://<api on the private network>
```

Run it from inside the private network (the voice endpoint only answers allowed networks). It checks health
and readiness; uploads a fictional public document with a unique word; waits for processing; publishes it; asks
as a guest and gets it cited; opens the citation's signed file link; asks through the voice contract; deletes
the document and checks that its link is gone and it no longer answers. It deletes the document even when a
step fails. Use dedicated smoke-test accounts in production. Its questions are real guest traffic, so they appear
on Quality (the final "smoke check word" question as unanswered, since its document was just deleted); that's
expected after a deploy.

## 9. Limits, budgets and monitoring

- **Rate limits** (operations.md §8 for alerts): per guest session, per user, all guests together, per IP at the
  BFF, sign-in lockout, five active ingestions per tenant. Tune with `RATE_LIMIT_*`.
- **Cost:** the local models cost nothing per question, so the spend limit is $0 and `LLM_DAILY_BUDGET_USD`
  stays unset. With a paid provider, it opens a critical `model_budget` alert when the last 24 hours' estimated
  spend reaches it (prices from `LLM_PRICE_*`). It alerts, it doesn't stop answering: set a hard monthly limit at
  the provider as well.
- **Monitoring:** poll `GET /api/v1/health` from outside every minute (it covers the worker and the queue);
  alerts go to the log and `ALERT_WEBHOOK_URL`; the Operations page has the rest. Ship container logs to the
  provider's log service with ≤ 30 days retention (they hold IDs and codes only).

## 10. §16 checklist status

| §16 item | Built and rehearsed | Still needs the real host |
| --- | --- | --- |
| Domains, HTTPS, CORS, cookies | §2 | The domain, its certificate, the edge config |
| Managed Postgres, backups, limited credentials | §3 | The managed instance and its backup policy |
| Persistent Chroma, monitoring, backup/rebuild, private network | §1, §7, operations.md §3 | The volume and network on the host |
| Durable object storage, permissions, lifecycle | §4 | The bucket, its policy and IAM roles |
| Durable worker with monitoring | heartbeats, alerts, restart policy | The orchestrator's restart policy |
| Separate service accounts | §3, §6 | Provider IAM roles |
| Reproducible, reversible migrations | §7 | — |
| Rate limits, cost budgets, budget alerts | §9 ($0 with local models) | — |
| Verify deletion and source access after deploy | §8, rehearsal check 7 | Run it there |
| Smoke test | §8 | Run it there |
| Rollback for app and index | §7, drill | — |

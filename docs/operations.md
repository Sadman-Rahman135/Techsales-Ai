# KnowledgeAI operations runbook (blueprint §11.3, §13, §16)

How to check, back up, restore, rebuild and stop the system. Commands run from the repo root unless they say
`cd backend`. Decisions behind them: ADR-002 (Postgres is the truth), ADR-012 (worker leases), ADR-013 (index
fingerprint), ADR-019 (retention), ADR-020 (hardening), ADR-021 (metrics, traces and alerts), ADR-023 (containers).

## 1. Is it healthy?

`GET /api/v1/health` returns 200 when every check passes and 503 otherwise, with one entry per component:

| Check | Healthy when | If not |
| --- | --- | --- |
| `postgres` | `SELECT 1` answers within 2 s | Start the database (`docker compose up -d`); check `DATABASE_URL` |
| `chroma` | `/api/v2/heartbeat` answers | Start Chroma; answers fail safely with "Something went wrong" until it's back |
| `storage` | The upload directory (or bucket) is writable | Disk full or permissions |
| `worker` | A worker heartbeat is newer than `WORKER_STALE_SECONDS` (60) | Start a worker: `cd backend; .venv/Scripts/python -m app.ingestion.worker`. Uploads wait ("Waiting") until one runs |
| `queue` | The oldest job that could run has waited less than `QUEUE_MAX_WAIT_SECONDS` (900) | Workers are too few or stuck: check `data/worker.log` (or the worker's logs), add a worker |

Details never contain hosts or credentials: an exception type, or a fixed sentence with a number.

## 2. Backup and restore (Postgres)

Postgres holds everything that can't be rebuilt. Chroma is rebuilt from it (§4), and uploaded originals live in object
storage (back up the bucket with its own versioning/lifecycle rules).

```sh
backend/.venv/Scripts/python scripts/db_backup.py backup --verify   # dump to data/backups/, then a restore check
backend/.venv/Scripts/python scripts/db_backup.py verify data/backups/knowledgeai-<time>.dump
```

`verify` restores the dump into a scratch database (`knowledgeai_restore_check`), compares the schema revision and the row
count of every table with the source, checks the RLS policies came back, and drops the scratch database. A damaged
dump fails it. To restore for real (local stack):

```sh
docker compose exec -T postgres dropdb -U knowledgeai knowledgeai
docker compose exec -T postgres createdb -U knowledgeai -O knowledgeai knowledgeai
docker compose exec -T postgres pg_restore -U knowledgeai -d knowledgeai --exit-on-error < data/backups/<file>.dump
cd backend; .venv/Scripts/alembic check            # schema matches the code
.venv/Scripts/python -m app.ingestion.rebuild       # bring Chroma back in line with the restored rows
```

Staging and production use the managed database's point-in-time backups (M7); run the same `verify` against a restored
copy at least monthly and after every major migration.

## 3. Rebuild the vector index

Needed after a restore, a lost Chroma volume, or an embedding model change (the index refuses a different model,
ADR-013). Reads every live version from Postgres and the stored originals:

```sh
cd backend; .venv/Scripts/python -m app.ingestion.rebuild                  # into CHROMA_COLLECTION
cd backend; .venv/Scripts/python -m app.ingestion.rebuild --drop-existing  # development only: replace it
```

For production, rebuild into a new `CHROMA_COLLECTION`, then switch the setting. The document detail page's **Chunks** tab
shows, per version, whether Postgres and Chroma agree; a version that disagrees can't be published.

## 4. Retention and deletion (ADR-019)

Runs in the worker's maintenance cycle; run it once with `cd backend; .venv/Scripts/python -m app.ingestion.worker --retention`.

| What | When | Where it is removed |
| --- | --- | --- |
| A deleted document | `DELETED_DOCUMENT_PURGE_DAYS` (30) after deletion | Chroma vectors, stored originals, Postgres rows (citations keep pages, lose the quote) |
| A conversation | On "Delete chat" / "Clear history", or after `CONVERSATION_RETENTION_DAYS` (180) without activity | Messages, answers, citations and feedback (Postgres cascade); nothing else holds chat content |
| Audit events | Never (append-only) | — |
| Answer traces (§13.2) | With their conversation: they live on the answer row | Same as the conversation |
| Request metrics, resolved alerts | After `METRICS_RETENTION_DAYS` (90); open alerts are kept | `request_metrics`, `alert_events` rows |
| Logs | The log platform's retention (set it to ≤ 30 days; logs hold IDs and codes only) | — |

Every purge or bulk expiry writes an audit event (`document.purged`, `retention.conversations_deleted`); the metrics
expiry is counted in the retention summary (`metric_rows_deleted`).

## 5. Stopping and recovering

Releases and rollbacks (app, index, data) are in [deployment.md §7](deployment.md); after either, run the
smoke test (deployment.md §8).


- **Worker:** SIGINT/SIGTERM (Ctrl+C, `docker stop`) finishes the current job, then exits and removes its heartbeat. A
  worker that is killed or crashes leaves its job leased: when `locked_until` passes, reconciliation requeues it (or fails
  it when no attempts are left), and a newer attempt can't be overwritten by the old one (ADR-012).
- **API:** uvicorn stops accepting connections and finishes in-flight requests. Streams in progress end; the browser shows
  "Something went wrong" with Try again, and the question is already saved.
- **Reconciliation** (every `RECONCILE_INTERVAL_SECONDS`, or `python -m app.ingestion.worker --reconcile`) removes orphaned
  files and vectors and reports count drift.

## 6. Logs and traces

Logs carry request IDs, job IDs, codes and timings, never question or document text. A filter on every handler scrubs
tokens, signed file links, search terms, emails, phone, order and card numbers (`app/core/log_hygiene.py`). To follow
one request: the customer's "Details → Reference: req-…" is the `request_id` on the answer row and in every log line.

Each answer row is its trace (ADR-021): `trace_id`, `intent`, `graph_path`, `retrieved_chunk_ids`, `timings` per node,
`caller_role`, `error_code`, `token_usage` and the model ID on the message. To read one (as the owner role, with the tenant
set; never paste the result anywhere public, the question text is personal data):

```sql
SELECT a.request_id, a.caller_role, a.intent, a.graph_path, a.answer_status, a.error_code, a.retrieved_chunk_ids,
       a.timings, a.token_usage, m.model_id
FROM answers a JOIN messages m ON m.id = a.message_id WHERE a.request_id = 'req-…';
```

## 7. Scanning and updates

CI (`.github/workflows/ci.yml`) runs gitleaks on the history, `pip-audit` on `backend/constraints.txt` and
`npm audit --omit=dev`. Upgrades are made by hand: follow `docs/dependency-versions.md`, then run the tests.
One accepted exception is tracked in ADR-020 (`chromadb`, no fixed release): Chroma must never be reachable from outside
the app network.

## 8. Metrics and alerts (§13, ADR-021)

**Operations** (staff sidebar, managers and admins) shows the numbers for a date range: questions, latency p50/p95 per
step, tokens and estimated cost per question, zero-result, refusal and error rates, error codes, citation failures,
correction reports ("Not helpful"), the document pipeline (jobs, failures, retries, indexing lag), API errors (5xx, 429,
403, 401, top failing routes), the queue, and alerts. The same data: `GET /api/v1/admin/metrics?start=…&end=…`.
Evaluation runs (`caller_role = evaluation`) are never counted.

The worker checks three rules every `ALERT_CHECK_SECONDS` (60) over the last `ALERT_WINDOW_MINUTES` (15):

| Rule | Fires when | First steps |
| --- | --- | --- |
| `ingestion_failures` (critical) | ≥ `ALERT_INGESTION_FAILURES` (5) jobs failed | Documents list, status Failed: read the reason. Many "scanned PDF"/"password" reasons = bad uploads, tell the uploader. `EMBEDDING_*` or timeouts = the embedding provider (see §10). Retry from the list once fixed |
| `error_rate` (critical) | ≥ `ALERT_MIN_REQUESTS` (20) requests and ≥ `ALERT_ERROR_RATE` (5%) were 5xx | Operations → "Top failing routes"; `GET /api/v1/health`; the API log for those routes (request IDs, exception types) |
| `queue_stuck` (warning) | The oldest claimable job waited longer than `QUEUE_MAX_WAIT_SECONDS` | §1 `queue` row: start or add a worker |
| `model_budget` (critical) | `LLM_DAILY_BUDGET_USD` is set and the last 24 hours' estimated model spend reached it | Operations → Tokens and cost: a traffic spike, a loop, or abuse (check guest traffic and rate limits). It doesn't stop answering; the provider's hard limit does (deployment.md §9) |

An alert notifies once when it fires and once when it resolves: an `ERROR` log line `ALERT fired <rule> …` and, if
`ALERT_WEBHOOK_URL` is set, a Slack-compatible message. It stays open (with current numbers) until the condition
clears. A stopped worker evaluates nothing: `GET /health` (`worker` check) is what catches that, so monitor it from
outside (M7).

## 9. Incident: a wrong or invalid citation

Invalid citations (an ID that wasn't retrieved, a quote that isn't in the source, an employee-only source for a public
user) are blocked before anyone sees them: the answer becomes "Not in our documents" and the trace gets
`error_code = CITATIONS_INVALID`. A rising **Citation failures** count means the model or the prompt changed behaviour,
not that users saw bad citations. An *incident* is a citation that passed and is still wrong (a customer or employee
reports it, or a G2 audit finds it).

1. Get the reference (`req-…`) from the reporter ("Details → Reference"); Quality's "Not helpful" list shows which
   answers people flagged. Find the answer row (§6) and its `answer_citations` (document, version, pages, quote).
2. Open the document's **Chunks** tab for that version: is the quoted text there, on that page? Is that version the
   published one?
3. Decide which case it is:
   - **The document is wrong or outdated:** upload a corrected version and publish it. New answers cite it at once;
     old answers keep pointing at the archived version, which is correct as a record.
   - **The source is right but the answer misread it:** add the question to `evaluation/exploration/` (and to the next
     golden set, never golden-v1), reproduce, fix, and run the golden gates.
   - **A source the person may not see was cited:** treat it as a security incident. Run the access suite
     (`--suites access`), check `caller_role` and audience on the answer, and have an admin delete the document (it
     stops answering at once; there is no unpublish) until it is explained.
4. Record what happened: publishing and deleting are in the document's Activity (audited); a code fix goes in its
   commit.

## 10. Incident: model provider outage

**Signs:** answers show "Something went wrong while answering…" (status `error`); Operations shows the error rate rising
with codes `TIMEOUT` or `PROVIDER_FAILED`; embedding outages also show `RETRIEVAL_FAILED` and failed ingestion jobs
(the `ingestion_failures` alert). The API itself stays up: answers return 200 with status `error`, so the 5xx alert does
not fire for this. Watch the error rate on Operations.

**What the system already does:** one retry for the LLM, bounded backoff for embeddings, a 20 s model timeout inside a
30 s request deadline. No answer is invented: a failed call never produces citations. The question is saved, so people
can press Try again. Voice callers get the contract's `error` status and their own fallback.

**Steps:**
1. Check the provider's status page and our logs (`PROVIDER_FAILED:<type>` codes, no message bodies).
2. If it is a quota or key problem (401/429 from the provider), fix the key or quota: `LLM_API_KEY` in the secret store,
   then restart the API.
3. If the outage is long, tell support so they expect more contacts. Ingestion jobs retry on their own (3 attempts with
   backoff); retry the ones that ran out of attempts from the Documents list afterwards.
4. Changing to another model is a configuration change (`LLM_MODEL`), but changing the **embedding** model needs a
   rebuild into a new collection (§3, ADR-013): never switch embeddings as a quick fix.
5. Afterwards, run the golden gates against the restored provider before calling it resolved.

## 11. Containers and the load test (§15 M7, ADR-023)

**Run the release images** next to the data services. They read secrets from `.env`, reach Postgres and Chroma by
service name, and keep uploaded originals in the `uploads` volume:

```sh
docker compose --profile app up -d --build        # migrate (once) → api, worker → web
docker compose --profile app ps                   # api and web report (healthy); migrate has exited 0
curl http://127.0.0.1:18000/api/v1/health         # readiness: every component
curl http://127.0.0.1:18000/api/v1/health/live    # liveness: the container health check uses this one
docker compose --profile app logs -f worker       # ingestion, reconcile, retention, alerts
docker compose --profile app down                 # stop (volumes kept)
```

Web is on `http://127.0.0.1:13000`, the API on `http://127.0.0.1:18000`, so `npm run dev` and `fastapi dev` can run
beside them on the same database. Originals uploaded through `fastapi dev` live in `data/uploads/`, those uploaded
through the containers in the `uploads` volume: a document uploaded one way can't show its original in the other.
`web` can only reach `api` (network `app`); Postgres and Chroma are on `data`. To let a voice client call the
containerized API, add its network to `INTERNAL_API_ALLOWED_NETWORKS`.

**Load test (G9).** Guest traffic is recorded like real traffic, so run it against a separate API container that
answers for the evaluation tenant, with a raised guest budget (the limiter has its own tests):

```sh
docker compose --profile app run -d --rm --name knowledgeai-api-load -p 127.0.0.1:18001:8000   -e PUBLIC_ORGANIZATION=techcare-golden -e RATE_LIMIT_PUBLIC_PER_MINUTE=100000 -e RATE_LIMIT_ANONYMOUS_PER_MINUTE=100000 api
cd backend; .venv/Scripts/python -m app.evaluation.load --base-url http://127.0.0.1:18001 --organization techcare-golden   --out ../evaluation/reports/load.json       # 200 sequential + 100 at 20 concurrent, golden-v1 guest questions
docker stop knowledgeai-api-load
```

It reports p50/p95 of retrieval, time to first token and full answer, errors, throughput and tokens per answer. The
golden tenant must exist (the golden runner's `--setup --load-corpus` creates it). Compare with the proposed SLO in
`docs/evaluation.md` (G9); with the real provider the model time will dominate.

**Dependency lock.** After changing a pin: `docker compose --profile app build migrate`, then
`backend/.venv/Scripts/python scripts/lock_backend.py`, then all tests. `--check` tells you whether the lock is current.

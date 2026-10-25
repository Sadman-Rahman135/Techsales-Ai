# Evidence: §13 metrics, logs and admin operations (2026-10-20)

What was built for §13, how it is tested and what is still open. Decision: ADR-021 (traces on answer rows, per-minute
request counts, worker-evaluated alerts, 90-day metrics retention). Runbook: `docs/operations.md` §3 (vector rebuild),
§6 (traces), §8 (metrics and alerts), §9 (citation incidents), §10 (model-provider outage).

## Automated tests

`cd backend; .venv/Scripts/python -m pytest` → **467 passed** (0 skipped, 2 min 22 s) (see `12-evaluation.md`). New for §13:
`tests/integration/test_observability.py` (9).

| Test | Proves |
| --- | --- |
| `test_every_answer_records_the_trace_shape` | Each answer row has `request_id`, `trace_id`, `caller_role`, intent, graph path, retrieved **IDs**, per-node timings, token usage with estimated cost, model ID; no question or document text in any trace field |
| `test_provider_failures_get_a_sanitized_code` | A provider exception with secret-looking text becomes `PROVIDER_FAILED` (kind only) |
| `test_requests_are_counted_by_route_template` | `/api/v1/documents/{document_id}`, never the real ID, with the response status |
| `test_flushes_from_several_processes_add_up` | Two processes flushing the same minute add their counts (upsert) |
| `test_metrics_api_reports_tenant_and_service_numbers` | `GET /api/v1/admin/metrics`: §13.1 numbers; managers yes, employees 403 |
| `test_evaluation_traffic_stays_out_of_analytics` | `caller_role = evaluation` answers are in neither Quality nor Operations |
| `test_an_ingestion_failure_spike_fires_once_notifies_and_resolves` | Fires at the threshold, notifies once (log + webhook body), stays one open alert, resolves when the window clears |
| `test_an_abnormal_server_error_rate_fires` | 4 of 40 requests failing fires; a quiet window with one 503 (100%, but under the minimum count) doesn't, and resolves the alert |
| `test_metrics_and_resolved_alerts_expire` | Retention deletes old request metrics and resolved alerts, keeps open ones |

## §13.1 Application metrics

All on **Operations** (`/admin/operations`, managers and admins, date range) and in `GET /api/v1/admin/metrics`.

| Metric (§13.1) | Where it comes from |
| --- | --- |
| Questions per day, active users and organizations, latency p50/p95 | Answer rows; latency per graph step (retrieve, grade, generate, …) |
| Embedding and LLM tokens, estimated cost per question | `answers.token_usage` (provider usage metadata; estimated for the fake model) × list prices in settings (0 for the fake providers) |
| Zero-result rate, refusal rate | `error_code = NO_RESULTS`; `insufficient_evidence` share |
| Recall@k on the evaluation set | The evaluation report (`12-evaluation.md`: G1 98 / 98), not live traffic |
| Citation validity failures, correction reports | `error_code = CITATIONS_INVALID`; "Not helpful" feedback |
| Queue depth, parse failures, embedding retries, indexing lag | Worker heartbeats; failed jobs by code; attempts > 1; publish-ready minus upload time |
| Tool failures, permission denied, error rate, rate limited | Request metrics: 5xx, 403, 401, 429 and top failing routes. Tool failures: no tools in P0 (order lookups are P1), shown as 0 |

## §13.2 Trace shape and the checkboxes

| Item | Status | What and where | Tests | Manual |
| --- | --- | --- | --- | --- |
| Trace shape | ✓ | Answer rows (ADR-021); role surrogate, IDs only; model on the message | `test_every_answer_records_the_trace_shape`, `…sanitized_code` | runbook §6 query |
| Alerts for ingestion failure spikes or abnormal 5xx rate | ✓ | `app/services/alerts.py`, run by the worker every 60 s; also a stuck-queue warning. Log at ERROR + optional Slack-compatible webhook (`ALERT_WEBHOOK_URL`, a secret) | the three alert tests above | below |
| Runbook: vector rebuild, invalid citations, provider outage | ✓ | `docs/operations.md` §3, §9, §10 | — (procedures) | §3 rebuild was run in §11 (`11-security-reliability.md`) |
| Retention for traces as well as documents and messages | ✓ | Traces live on answer rows and go with their conversation (ADR-019: on delete or after 180 days idle); request metrics and resolved alerts after 90 days; logs hold IDs only | `test_metrics_and_resolved_alerts_expire`, `test_operations::test_retention_purges_every_store` | — |

## Manual test (dev stack, 2026-10-20)

1. As Priya, uploaded five copies of a scanned (image-only) PDF. Each failed with "We couldn't read this PDF…".
2. Within a minute the worker logged:
   `2026-10-20 04:04:34,878 ERROR app.alerts ALERT fired ingestion_failures [critical]: 5 documents failed to process in the last 15 minutes.`
3. Operations showed the open critical alert banner, the failures in the document pipeline and the alert history:
   `13-01-operations-alert-light.png`; dark mode `13-02-operations-dark.png`; phone width `13-03-operations-mobile.png`.
4. Nothing else failed. Once the failures were older than the 15-minute window, the worker (restarted at 04:18 with
   the change below) closed the alert on its first check:
   `2026-10-20 04:19:34,290 INFO app.alerts ALERT resolved ingestion_failures [critical]: 5 documents failed to process in the last 15 minutes.`
   The open alert didn't depend on the old process: the restarted worker found and resolved it from Postgres.

The Operations page was checked against the `design-system/MASTER.md` checklist in light, dark and phone width (the
three screenshots): semantic tokens only, lucide icons, every status shown with an icon and text, numbers as tables.

## Changes after review

- `ALERT_WEBHOOK_URL` is a `SecretStr` (a webhook URL is a credential), and a notification without an injected client
  now closes the HTTP client it opens.

## Not done here

- No alert fires for a model-provider outage, because answers return 200 with status `error`, not a 5xx. The runbook (§10)
  says to watch the error rate on Operations; an answer-error-rate rule is a small follow-up (P1).
- Service-wide numbers (API errors, queue, alerts) are shown to each tenant's managers; right for one tenant, needs a
  platform-operator role before tenants share a deployment (ADR-021).
- Exporting to a metrics backend (Prometheus/OpenTelemetry) is P1; it can read the same tables.
- Monitoring that the worker itself is alive is external (`GET /health`, M7).

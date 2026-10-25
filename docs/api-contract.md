# KnowledgeAI HTTP API (blueprint §9, §14)

For developers calling the API: the Next.js BFF, the voice project (Project 2), test clients and operators.
The **machine-readable contract** is [`contracts/openapi.json`](contracts/openapi.json) (OpenAPI 3.1, generated
from the code). The voice endpoint also has its own frozen schema and client rules:
[`contracts/voice-knowledge-api.md`](contracts/voice-knowledge-api.md).

Both files are checked by `cd backend; .venv/Scripts/python -m app.cli.contracts` and by the unit tests, so a route
or schema change fails CI until the file is regenerated (`--write`) and the diff reviewed. The interactive docs
(`/docs`, `/redoc`, `/openapi.json`) are served in development only (ADR-020).

## 1. Conventions

- **Base path:** every route is under `/api/v1`. Breaking changes get `/api/v2`; v1 keeps working until callers move.
- **Who calls it:** browsers never do. They call the Next.js BFF, which holds the API token in an httpOnly cookie
  (ADR-005, ADR-014). Direct callers are the BFF, the voice service and test or operator tools.
- **JSON** in and out, UTF-8. Timestamps are ISO 8601 UTC (`2026-10-20T05:37:43Z`); IDs are UUIDv7 strings.
- **Request IDs:** every response carries `X-Request-ID`, and every error body repeats it. Quote it in bug reports;
  it's the `request_id` on the answer trace and in every log line (docs/operations.md §6).
- **Tenant:** always taken from the credential, never from the request. A caller can't name another organization.

## 2. Authentication

| Caller | How | Gets |
| --- | --- | --- |
| Staff and signed-in customers | `POST /auth/login` with email and password, then `Authorization: Bearer <access_token>` | Their role in their organization |
| Guests (public chat) | No token; send `X-Session-Ref: <22–128 chars of [A-Za-z0-9_-]>`, a random value the client keeps per browser | Public documents of `PUBLIC_ORGANIZATION`; their own conversations |
| Voice service | `Authorization: Bearer <INTERNAL_SERVICE_TOKEN>`, **and** the caller's IP inside `INTERNAL_API_ALLOWED_NETWORKS` | `/internal/knowledge/answer` only, public audience only |

A user token can't call the service route and the service token can't call user routes. Tokens expire (`expires_at` in
the login response); then sign in again.

## 3. Routes

Roles follow the permission matrix (docs/product-vision.md §3). "Guest" means a valid `X-Session-Ref`.

| Method and path | Who | Purpose |
| --- | --- | --- |
| `POST /auth/login` | anyone | Sign in; returns `access_token`, `expires_at`, `me`. Repeated failures lock the account for a while |
| `GET /me` | signed in | The caller's user, role and organization |
| `POST /chat/ask` | guest, signed in | Answer one question (JSON) |
| `POST /chat/ask/stream` | guest, signed in | The same answer as server-sent events (§5) |
| `GET /conversations` · `GET /conversations/{id}` | guest, signed in | The caller's own conversations, with answers and citations |
| `DELETE /conversations/{id}` · `DELETE /conversations` | guest, signed in | Delete one conversation, or all of the caller's |
| `POST /answers/{id}/feedback` | guest, signed in | Helpful / not helpful (+ optional note) on the caller's own answer |
| `GET /documents` · `GET /documents/{id}` | signed in | Managers and admins: everything. Others: published documents of their audiences |
| `GET /documents/facets` | signed in | Filter values (tags, product models) for the same visibility |
| `GET /documents/{id}/preview` | guest, signed in | A short-lived signed link to the cited original (ADR-018) |
| `GET /files/{token}` | holder of the link | The original file; the token is the authorization |
| `POST /documents` | manager, admin | Upload a new document (multipart: `file`, `title`, `audience`, …); queues ingestion |
| `POST /documents/{id}/versions` | manager, admin | Upload a new version of a document |
| `POST /documents/{id}/publish` | manager, admin | Publish a ready version; the previous one is archived |
| `DELETE /documents/{id}` | admin | Delete a document: it stops answering at once, and is purged after 30 days |
| `GET /documents/{id}/activity` | manager, admin | The document's audit trail |
| `GET /documents/{id}/versions/{version_id}/chunks` | manager, admin | Chunks and Postgres/Chroma agreement for a version |
| `GET /ingestion-jobs/{id}` · `POST …/cancel` · `POST …/retry` | manager, admin | Follow, stop or retry processing |
| `GET /admin/analytics` | manager, admin | Quality dashboard numbers for a date range |
| `GET /admin/metrics` | manager, admin | Operations numbers for a date range (§13.1, ADR-021) |
| `GET /admin/reviews` | manager, admin | The review queue: unanswered questions and not-helpful answers (`status`, `reason`, paging; counts per status), redacted (ADR-030) |
| `PUT /admin/reviews/{answer_id}` · `DELETE …` | manager, admin | Resolve or dismiss (optional note and document) · reopen; audited |
| `GET /admin/exports/{dataset}` | manager, admin | CSV of `questions`, `feedback`, `reviews`, `tickets` (date range) or `documents`; redacted and audited |
| `POST /orders/lookup` | guest, signed in | An order's status, only with the email or phone used at checkout (ADR-029). Repeated mismatches lock the caller and the order number. With the caller's `conversation_id`, follow-up questions in that chat may use the order for 30 minutes (ADR-031) |
| `GET /orders/mine` | signed in | A signed-in customer's own orders, newest first |
| `POST /repairs/lookup` · `GET /repairs/mine` | guest, signed in · signed in | The same for repair jobs (`RP-123456` + the contact on the job), ADR-031 |
| `POST /tickets/draft` | employee, manager, admin | A subject and summary drafted from the caller's own conversation, for review; nothing is saved |
| `GET /tickets` · `POST /tickets` | employee, manager, admin | The ticket queue (`status`, `mine`, paging; counts per status) · file a reviewed ticket |
| `GET /tickets/{id}` · `PATCH /tickets/{id}` · `POST /tickets/{id}/notes` | employee, manager, admin | A ticket with its notes · change status, priority or assignee · add a note (all audited) |
| `GET /tickets/staff` | employee, manager, admin | Who a ticket can be assigned to |
| `POST /internal/knowledge/answer` | voice service | `knowledge-answer.v1` (frozen) |
| `GET /health` | anyone | Readiness: postgres, chroma, storage, worker, queue; 503 when any fails |
| `GET /health/live` | anyone | Liveness: the process answers HTTP (container health checks) |

## 4. Errors

Every error has the same body, with the status codes of §9.3:

```json
{"error": {"code": "UNAUTHENTICATED", "message": "Sign in to continue.", "request_id": "req-85ce…"}}
```

| Status | When | Code examples |
| --- | --- | --- |
| 400 | The body isn't valid JSON | `MALFORMED_REQUEST` |
| 401 | No, wrong or expired credential | `UNAUTHENTICATED`, `INVALID_CREDENTIALS` |
| 403 | Signed in, but the role may not do this; voice caller outside the allowed networks | `FORBIDDEN` |
| 404 | Not found **or not visible to the caller** (the two are never distinguished); an order whose number and contact don't match (never says which) | `NOT_FOUND`, `ORDER_NOT_VERIFIED`, `REPAIR_NOT_VERIFIED` |
| 409 | Duplicate upload, or a new document titled like a live one (confirm with `allow_same_title`); publishing a version that isn't ready or whose index disagrees with Postgres | `DUPLICATE_FILE`, `DOCUMENT_TITLE_EXISTS`, `VERSION_INVALID_TRANSITION`, `VERSION_NOT_FULLY_INDEXED` |
| 413 | Upload over `MAX_UPLOAD_BYTES` | `FILE_TOO_LARGE` |
| 422 | Invalid fields, or a file that fails validation; an export over 50,000 rows | `VALIDATION_ERROR`, `EXPORT_TOO_LARGE`, `FILE_TYPE_NOT_ALLOWED`, `FILE_TYPE_MISMATCH`, `FILE_ENCRYPTED`, `FILE_MALWARE_DETECTED`, … |
| 429 | Rate limit, a locked account, or too many uploads processing; `Retry-After` header and `details.retry_after_seconds` | `RATE_LIMITED`, `LOGIN_LOCKED`, `ORDER_LOOKUP_LOCKED`, `TOO_MANY_ACTIVE_INGESTIONS` |
| 500 / 503 | Our failure: no details, only the request ID | `INTERNAL_ERROR`, `SCANNER_UNAVAILABLE` |

`message` is written for people (design-system/content-guide.md); program against `code`. The full list of codes is
in the OpenAPI document's error responses.

**An answer that couldn't be given is not an HTTP error.** `POST /chat/ask` returns 200 with
`answer_status` = `answered`, `insufficient_evidence` ("Not in our documents", no citations), `direct` (greeting,
support handoff, or an order question: then `next_action` is `verify_order` and the client shows the
order check, which calls `POST /orders/lookup`; or, when the caller's own orders/repairs answer it,
`next_action` is `show_records` and `records` holds them, ADR-031) or `error` (a model or index failure; the question is saved, try again).

## 5. Streaming answers

`POST /chat/ask/stream` (same body as `/chat/ask`) returns `text/event-stream`:

| Event | Data | Meaning |
| --- | --- | --- |
| `meta` | `request_id`, `trace_id` | First event |
| `status` | `stage`: `searching`, `rewriting`, `checking` | Progress, for the UI |
| `delta` | `text` | Provisional text, sent only after the evidence passed |
| `final` | The full `AskResult` (as `/chat/ask` returns) | **Authoritative:** replace anything shown from `delta` with it (ADR-006) |
| `error` | The §4 error body plus `retryable` | The stream failed after it started |

Refusals (401, 404, 429) happen before the stream starts, as normal JSON errors.

## 6. Sample requests

Replace the values in angle brackets. Local stack: API on `http://127.0.0.1:8000` (`fastapi dev`) or
`http://127.0.0.1:18000` (containers). The demo accounts' passwords are in `data/dev-credentials.txt` (git-ignored).

**Sign in and call an authenticated route:**

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "priya.manager@techcare.example", "password": "<password>"}'
# → {"access_token": "<jwt>", "token_type": "bearer", "expires_at": "…", "me": {"role": "manager", …}}

curl -s http://127.0.0.1:8000/api/v1/me -H 'Authorization: Bearer <jwt>'
curl -s 'http://127.0.0.1:8000/api/v1/documents?limit=5' -H 'Authorization: Bearer <jwt>'
```

**Upload and publish a document (manager):**

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/documents -H 'Authorization: Bearer <jwt>' \
  -F 'file=@data/demo-docs/TechCare-Store-Hours.txt' -F 'title=Store hours' -F 'audience=public'
# → 202 {"document_id": "<document_id>", "version_id": "<version_id>", "version_number": 1, "job": {"id": "<job_id>", …}}
curl -s http://127.0.0.1:8000/api/v1/ingestion-jobs/<job_id> -H 'Authorization: Bearer <jwt>'   # until "done"
curl -s -X POST http://127.0.0.1:8000/api/v1/documents/<document_id>/publish -H 'Authorization: Bearer <jwt>' \
  -H 'Content-Type: application/json' -d '{"version_id": "<version_id>"}'
```

**Ask as a guest (JSON, then streamed):**

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/chat/ask \
  -H 'Content-Type: application/json' -H 'X-Session-Ref: <random-22+-chars>' \
  -d '{"question": "How should I bring a swollen battery for recycling?"}'
# → {"answer_status": "answered", "grounded": true,
#    "answer": "Tape over the battery terminals before you bring a battery in. [1] …",
#    "citations": [{"position": 1, "title": "Battery Recycling Drop-off", "page_start": 1, "quote": "…", …}], …}

curl -N -X POST http://127.0.0.1:8000/api/v1/chat/ask/stream \
  -H 'Content-Type: application/json' -H 'X-Session-Ref: <random-22+-chars>' \
  -d '{"question": "Do you sell drones?"}'
# event: meta … event: status … event: final  data: {"answer_status": "insufficient_evidence", "citations": [], …}
```

**Voice service** (from an allowed network; see [`contracts/voice-knowledge-api.md`](contracts/voice-knowledge-api.md)):

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/internal/knowledge/answer \
  -H 'Authorization: Bearer <INTERNAL_SERVICE_TOKEN>' -H 'Content-Type: application/json' \
  -d '{"question": "How long is the laptop warranty?", "conversation_id": "call-123", "request_id": "voice-1"}'
```

These requests were run against the local container stack on 2026-10-20 (docs/demos/14-15-repository-release.md).

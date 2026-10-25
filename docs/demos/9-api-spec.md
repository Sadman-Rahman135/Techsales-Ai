# Evidence: §9 API specifications (2026-10-19)

§9 lists the API surface. Sections 7 and 8 built most of it; this section completed the P0 routes that were missing,
froze the voice contract, and checked the conventions on **every** route.

| §9 route | Status | Built in |
| --- | --- | --- |
| `POST /auth/login`, `GET /me` | ✓ | §7 |
| `POST/GET /documents`, `GET /documents/{id}`, `POST /documents/{id}/versions`, `POST /documents/{id}/publish`, `DELETE /documents/{id}`, `GET /ingestion-jobs/{id}` | ✓ | §7 |
| `GET /documents/{id}/preview` (short-lived authorized file link) + `GET /files/{token}` | ✓ **new** | §9 |
| `POST /chat/ask` (+ `/chat/ask/stream`), `GET /conversations`, `GET /conversations/{id}`, `POST /answers/{id}/feedback` | ✓ | §8 |
| `GET /admin/analytics` | ✓ **new** | §9 |
| `POST /internal/knowledge/answer` | ✓, now versioned `knowledge-answer.v1` | §8, §9 |
| `GET /orders/{id}`, `POST /support/tickets` | P1, not built | — |

## Automated tests

`cd backend; .venv/Scripts/python -m pytest` → **361 passed** (60 new). `ruff check`, `ruff format --check`, `mypy --strict`
(88 files) pass.

| Requirement | Test(s) |
| --- | --- |
| Every route versioned under `/api/v1` and documenting the §9.3 error shape in OpenAPI | `test_api_spec::test_every_route_is_versioned_and_documents_the_error_shape` |
| Authenticated as required: every protected route refuses anonymous callers with 401 + `request_id` | `test_api_spec::test_every_protected_route_refuses_anonymous_callers` (one case per route, 19 routes) |
| §9.3 status codes: 400 malformed vs 422 invalid | `test_api_spec::test_malformed_json_is_400_and_invalid_json_is_422` |
| Permission matrix enforced in each handler (product-vision.md §3) | `test_api_spec::test_role_matrix` (11 routes × 4 roles), `…test_user_tokens_cannot_call_the_service_api` |
| §9.1 `preview_url` → preview → file, same caller | `test_api_spec::test_citation_preview_links_open_the_cited_file` |
| Preview role filtering (audience, drafts managers only), no ID probing | `test_api_spec::test_preview_respects_audience_and_draft_rules`, `…test_preview_ids_cannot_be_probed_or_mixed` |
| Links short-lived, scoped, revoked by deletion; safe content types | `test_api_spec::test_file_links_expire_and_are_revoked_by_deletion`, `…test_text_files_are_served_as_plain_text…`, `test_api_support::test_file_links_round_trip_and_expire`, `…and_session_tokens_are_not_interchangeable` |
| Analytics counts match the activity; tenant isolation | `test_analytics_api::test_counts_match_the_activity`, `…test_other_tenants_activity_is_never_counted` |
| Analytics: PII redacted, conversations restricted, missing topics | `test_analytics_api::test_unanswered_questions_are_listed_redacted`, `test_api_support::test_personal_data_is_redacted`, `…test_product_text_is_left_alone` |
| Analytics: feedback and ingestion failures | `test_analytics_api::test_feedback_and_ingestion_failures` |
| Analytics: date range in the tenant's timezone, label, validation, roles | `test_analytics_api::test_range_label_and_timezone`, `…test_bad_ranges_are_rejected`, `…test_only_managers_and_admins`, `test_api_support::test_range_labels_follow_the_dashboard_copy` |
| §9.2 contract versioned and frozen | `test_api_support::test_the_voice_contract_matches_its_frozen_schema`, `test_chat_api::test_voice_contract` |

## Manual test (running API, `docs/demos/9-api-transcript.txt`)

| # | Step | Expected | Result |
| --- | --- | --- | --- |
| 1 | Anonymous question → `preview_url` of the citation → preview | 200: page 2, active version, signed link with `#page=2` | ✓ |
| 2 | Open the link | The PDF at page 2 ("Paper jams"), `nosniff`, `no-store`, `no-referrer`, CSP sandbox | ✓ `9-01-citation-preview-pdf.png` |
| 3 | Tampered link / page 99 / employee-only document anonymously | 404 / 422 `PAGE_OUT_OF_RANGE` / 404 | ✓ |
| 4 | Employee previews the employee-only document | 200 | ✓ |
| 5 | Analytics as manager (dev data, 13–19 Oct) | 142 questions: 86 answered, 34 not in documents, 22 direct; p50 31 ms; label in store time | ✓ |
| 6 | Analytics as employee / reversed range | 403 / 422 `INVALID_RANGE` | ✓ |
| 7 | Voice call | `X-Contract-Version: knowledge-answer.v1` and the same `contract_version` field | ✓ |
| 8 | Truncated JSON / `question: 42` / no credentials | 400 `MALFORMED_REQUEST` / 422 with fields / 401 | ✓ |
| 9 | OpenAPI | All route groups including `analytics` and `internal` | ✓ `9-02-openapi.png` |

## Found and fixed while building

- The SSE route documented its errors as `text/event-stream`, although refusals happen before streaming and are JSON.
  The route-convention test caught it; the route now declares JSON error responses.
- Malformed JSON returned 422; §9.3 asks for 400 (malformed) vs 422 (validation). Now distinguished.
- Caller resolution lived in the chat module; preview needs it too, so it moved to `app/api/deps.py`, and the voice
  contract moved to `app/api/internal.py` (the §14 layout).

## Not done here

- P1 routes: order lookup and support tickets (US-05).
- ~~Analytics UI and the source panel / document preview tab~~: done in §10, see `10-ui.md`.
- `conversations:read`: no role holds it in the MVP, so full conversations never appear in analytics.
- Name and address redaction in analytics questions (needs NER, P1).

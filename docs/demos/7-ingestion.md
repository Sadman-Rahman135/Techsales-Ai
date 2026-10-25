# Evidence: §7 document ingestion pipeline (2026-10-19)

Stack under test: Postgres 18.6 + Chroma 1.5.9 (compose), FastAPI API, ingestion worker, Next.js 16.4 BFF, all local.
Embeddings use the deterministic `fake` provider (feature hashing, L2-normalized); the real provider is an open §19 decision.

## Automated tests

`cd backend; .venv/Scripts/python -m pytest` → **224 passed** (150 new for §7). `ruff check`, `ruff format --check` and
`mypy --strict` (57 files) pass. Frontend: `npm run lint`, `npm run typecheck` and `npm run build` pass. All pre-commit hooks pass,
including gitleaks.

| Plan item | Test(s) |
| --- | --- |
| §7.1 multipart upload returns the job at once | `test_documents_api::test_upload_returns_202_with_the_job_then_reports_progress` |
| §7.1 configurable size / page / concurrency limits | `test_validation::test_size_and_page_limits_are_configurable`, `test_documents_api::test_too_large_uploads_get_413`, `test_ingestion::test_concurrent_ingestions_per_tenant_are_limited` |
| §7.1 allowlist, content signatures, executables | `test_validation::test_extensions_outside_the_allowlist_are_rejected`, `…executables_renamed_to_an_allowed_extension_are_rejected`, `…contents_must_match_the_extension`, `…declared_mime_type_is_cross_checked…`, `…docx_must_be_a_word_package_without_macros` |
| §7.1 sanitized names, generated keys | `test_validation::test_filenames_are_sanitized_for_display`, `test_ingestion::test_upload_queues_a_job…` (key = `{org}/{document}/{version}.pdf`), `tests/unit/test_storage.py` |
| §7.1 malware scanning (interface) | `test_scanning.py` (builtin EICAR, clamd INSTREAM against a fake daemon, fail-closed), `test_documents_api::test_malware_is_blocked_and_audited_but_never_stored` |
| §7.1 uploader role and organization | `test_documents_api::test_upload_requires_a_manager_or_admin`, `…other_tenants_documents_and_jobs_are_invisible`, `…role_changes_apply_immediately…`, `test_ingestion::test_only_managers_and_admins_upload` |
| §7.1 SHA-256, size, source | `test_validation::test_allowed_files_pass_with_canonical_type_and_checksum`; the upload audit row records filename, size, checksum, pages |
| §7.1 encrypted / corrupt / empty / unsupported | `test_validation::test_password_protected_files_are_rejected`, `…corrupt_files_are_rejected`, `…empty_and_whitespace_only…`, `test_documents_api::test_rejected_uploads_get_a_stable_code_and_a_friendly_message` |
| §7.1 job status + friendly messages | `test_documents_api::test_failed_job_shows_the_step_and_can_be_retried`, `…validation_errors_use_the_shared_shape`, `…request_ids_from_the_bff_are_echoed` |
| §7.2 PDF pages and reading order | `test_parsing::test_pdf_segments_keep_heading_path_and_page`, `…pdf_tables_are_kept_row_by_row_in_reading_order` |
| §7.2 DOCX paragraphs, headings, tables, sections | `test_parsing::test_docx_headings_lists_tables_and_page_breaks` |
| §7.2 normalization without destroying tables | `test_text_cleanup.py` (7 normalization tests), `test_parsing::test_pdf_running_headers_and_page_numbers_are_removed` |
| §7.2 hierarchy heading → subsection → body → page | `test_parsing::*section_path*`, `test_chunking::test_chunks_never_cross_a_section` |
| §7.2 empty / low-confidence pages | `test_parsing::test_pdf_pages_without_text_are_flagged`, `…scanned_pdf_without_any_text_fails…`, `…pages_full_of_unreadable_characters…` |
| §7.2 secrets and personal records | `test_text_cleanup::test_secrets_and_personal_identifiers_are_redacted` (8 kinds), `…ordinary_policy_text_is_left_alone`, `test_parsing::test_secrets_are_redacted_and_counted_but_never_recorded` |
| §7.2 segment → page/section references | `test_ingestion::test_chunk_rows_keep_source_references` |
| §7.3 structure-aware sizes + overlap | `test_chunking::test_long_sections_respect_the_size_limits`, `…consecutive_chunks_overlap_by_whole_sentences`, `…large_tables_split_between_rows…` |
| §7.3 no mid-sentence splits | `test_chunking::test_splits_happen_between_sentences_never_inside_one`, `…a_single_huge_sentence_is_split_between_words` |
| §7.3 source metadata + stable hash | `test_chunking::test_checksums_are_stable_and_content_addressed`, `test_ingestion::test_chunk_rows_keep_source_references` |
| §7.3 batching, backoff, spend | `test_embeddings.py` (batching, 429/503 backoff with jitter + `Retry-After`, bounded give-up, no retry on 4xx, spend log) |
| §7.3 deterministic IDs, idempotent upserts | `test_ingestion::test_reprocessing_a_version_is_idempotent` |
| §7.3 counts and failures in Postgres | `test_ingestion::test_upload_queues_a_job_and_the_worker_makes_the_version_ready`, `…parse_errors_fail_at_once…` |
| §7.3 verify before publish | `test_ingestion::test_count_drift_is_reported` (publish refused), `test_versions::test_publish_refuses_when_chroma_is_missing_chunks` |
| §7.3 two versions + a deleted document | `test_ingestion::test_two_versions_and_a_deleted_document` |
| §7.3 never mix embedding models | `test_ingestion::test_an_index_refuses_a_different_embedding_model` |
| §7.4 states | see the state mapping in `docs/data-model.md` §7 |
| §7.4 retries vs non-retryable | `test_ingestion::test_transient_errors_retry_with_backoff_until_attempts_run_out`, `…backoff_grows_exponentially`, `…a_transient_error_then_success`, `…bad_embedding_configuration_is_not_retried`, `…parse_errors_fail_at_once_without_retry` |
| §7.4 no conflicting concurrent work | `test_ingestion::test_concurrent_workers_never_claim_the_same_job`, `…concurrent_publishes_leave_exactly_one_published_version`, `…an_expired_attempt_cannot_overwrite_the_new_one` |
| §7.4 cancellation before publication | `test_ingestion::test_cancel_a_queued_job`, `…cancel_while_processing_stops_at_the_next_stage`, `…cancel_a_ready_draft_discards_it_but_published_versions_cannot_be_cancelled` |
| §7.4 scheduled reconciliation | `test_ingestion::test_orphan_files_are_removed_only_after_the_grace_period`, `…expired_job_without_attempts_left_fails`, `…two_versions_and_a_deleted_document` (orphan vectors), `…count_drift_is_reported` |
| §7.4 latency and errors by stage | every finished job stores `stage_timings` (parsing, chunking, embedding, indexing, verifying, tokens) and `failed_stage` + `error_code` |
| Auth (M1) | `test_security.py` (argon2id, token round trip, 7 forged-token cases), `test_documents_api::test_login…`, `…bad_credentials_get_one_uniform_answer` |

## Manual test (browser, Chrome via DevTools; API + worker + web running locally)

| # | Step | Expected | Result | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Open `/admin/documents` signed out | Redirect to `/login?next=…` | ✓ | — |
| 2 | Sign in with a wrong password | "The email or password is incorrect." announced as an alert | ✓ | `7-01-login-error.png` |
| 3 | Sign in as manager | Admin shell, role shown, empty state with Upload | ✓ | `7-02-documents-empty.png` |
| 4 | Upload the warranty PDF with title, model, category | Limits stated before upload; stepper reaches Ready | ✓ | `7-03-upload-dialog.png`, `7-05-stepper-ready.png` |
| 5 | Publish v1 from the dialog | Confirmation states the effect; toast; row shows Published v1 | ✓ | `7-06-publish-confirm.png`, `7-07-published-toast.png` |
| 6 | Stop the worker, upload the returns DOCX (employees only) | Stepper: Uploaded ✓, Parsing current, "Waiting for a processing slot…" | ✓ | `7-04-stepper-processing.png` |
| 7 | Close the dialog while waiting | Row shows Processing · Waiting; focus returns to Upload | ✓ | `7-08-list-processing.png` |
| 8 | Restart the worker | List refreshes itself; toast "… is ready to publish." | ✓ | `7-09-ready-toast.png` |
| 9 | Upload a scanned PDF | Parsing step failed, content-guide copy, Retry + Choose another file | ✓ | `7-10-stepper-failed.png` |
| 10 | List in light and dark mode at 1440 px | All statuses are icon + text; contrast holds | ✓ | `7-11-list-light.png`, `7-12-list-dark.png` |
| 11 | 375 px phone | Cards instead of a table; `scrollWidth == clientWidth` (no horizontal scroll) | ✓ | `7-13-list-mobile.png` |
| 12 | Upload the same DOCX again as a new document | Duplicate warning with request ID; "Upload anyway" works | ✓ | `7-14-duplicate-warning.png` |
| 13 | That upload finishes | Warning: "Removed before indexing…: 1 payment card." (the demo DOCX contains a test card number) | ✓ | `7-15-ready-with-redaction-warning.png` |
| 14 | Esc in the dialog | Dialog closes, focus returns to the opener | ✓ | — |
| 15 | Sign in as an employee | "Managers and admins only" state | ✓ | `7-16-no-permission.png` |
| 16 | Search "returns" | Two matches; query kept in the URL (`?q=returns`) | ✓ | `7-17-search.png` |
| 17 | Admin deletes a document | Confirmation names it and the consequence; toast; row removed; audit row | ✓ | `7-18-delete-confirm.png`, `7-db-after-manual-test.txt` |
| 18 | Cross-site POST to a BFF route | 403 (no or foreign `Origin`); same-origin without a session → 401 | ✓ | curl, see below |
| 19 | API: second version, publish, reconcile, delete, reconcile | v1 archived and its vectors removed; delete is admin-only; vectors of the deleted document removed | ✓ | `7-versions-and-delete-transcript.txt` |
| 20 | Chunk rows of the returns DOCX | Section breadcrumbs and pages (Phones on page 2, after the page break) | ✓ | `7-db-after-manual-test.txt` |

```
origin='https://evil.example' -> 403
origin='' -> 403
same-origin, no session -> 401
```

## Found and fixed while building

- A Title and a "Heading 1" in Word both mapped to level 1, so the first heading replaced the document title in the section path. Title is now level 0.
- The worker saved stage timings before the indexing timer closed, so "indexing" was missing. Verification is now timed inside the final transaction.
- Reconciliation in tests reported other tests' committed versions as drift; the test now checks its own version.
- Alembic's `fileConfig` disabled the app's loggers whenever migrations ran in-process (so the spend log vanished in the full test run). `disable_existing_loggers=False`.
- Next.js flagged `crypto.randomUUID()` (request IDs) during prerender; BFF calls now `await connection()` first.
- Both modals used the same `id` for their title, so the publish confirmation was announced as "Upload document". IDs come from `useId()`.
- Field hints were part of each input's accessible name; they are now `aria-describedby` descriptions.
- A queued job read "Parsing…"; it now says "Uploaded. Waiting for a processing slot…".
- The React purity lint rejected `Date.now()` in render; relative times use the API response's `Date` header instead.

## Not done here (kept open in the plan)

- Real malware scanner: the clamd client is tested against a fake daemon only. Verify against ClamAV when staging exists (M7).
- Chunk-size benchmark, and the embedding model/dimension choice: both wait for the provider decision (§19) and the golden set (§11).
- ~~Rebuild command for a new embedding model~~: done in §8 (`python -m app.ingestion.rebuild`, see `8-rag-workflow.md`).
- ~~Admin UI: document preview, version timeline, owner/tags columns, publication warnings, pager with > 25 documents~~: done in §10, see `10-ui.md`.
- After deleting a row, focus falls back to the page instead of the next row.

# KnowledgeAI threat model (blueprint §11, §14, §17)

What we protect, from whom, how, and what is still open. Each control names the test that proves it, so this page
can't quietly drift from the code. Architecture and trust boundaries: [architecture.md §1](architecture.md#1-containers-and-trust-boundaries);
decisions: ADR-004 (authorization before retrieval), ADR-011 (RLS), ADR-014 (sign-in), ADR-016 (gates around the
model), ADR-018 (file links), ADR-020 (hardening). Review this page when a trust boundary, a role or a model changes,
and before each release (§16).

## 1. Assets

| Asset | Why it matters | Where it lives |
| --- | --- | --- |
| Employee-only documents (procedures, refund rules, discounts) | Leaking them to customers is the main business harm | Postgres rows, Chroma chunks, original files |
| Other tenants' data | One tenant must never see another's | Every tenant table (RLS), shared Chroma collection |
| Conversations and feedback notes | Customers' own words: personal data | `messages`, `answer_feedback` |
| Credentials and tokens | Session JWTs, the voice service token, DB passwords, provider keys | BFF cookie, `.env` / secret store |
| Answer integrity | A wrong policy stated as fact (refunds, warranties) costs money and trust | The RAG graph's output |
| Availability and spend | Model calls cost money; ingestion can be flooded | API, worker, provider account |

## 2. Actors

Anonymous visitor (anyone on the internet) · signed-in customer · employee · manager · admin · the voice service
(Project 2, service credential) · an insider uploading a malicious document · a compromised dependency · the model
provider (sees prompts).

## 3. Threats and controls (STRIDE by boundary)

### Browser ↔ web (BFF) ↔ api

| Threat | Control | Proof |
| --- | --- | --- |
| **S** Stolen or forged session | JWT only in an httpOnly, SameSite, Secure (production) cookie held by the BFF; the browser never sees it (ADR-005, ADR-014). Tokens are signed and expire | `test_security::test_invalid_tokens_are_rejected`, `…test_token_round_trip`, `test_api_spec::test_every_protected_route_refuses_anonymous_callers` |
| **S** Password guessing | argon2id hashes; the account locks after `LOGIN_MAX_FAILURES` | `test_operations::test_repeated_wrong_passwords_lock_the_account_for_a_while`, `test_hygiene::test_failure_lock_locks_after_the_limit_and_expires` |
| **E** A customer acting as staff | Role read from the database on each request, never from the request body; a role change applies at once | `test_api_spec::test_role_matrix` (11 routes × 4 roles), `test_documents_api::test_role_changes_apply_immediately_not_at_token_expiry` |
| **I** Guessing IDs of documents, files, conversations | 404 for both "missing" and "not yours"; files only through short-lived signed links (ADR-018) | `test_api_spec::test_preview_ids_cannot_be_probed_or_mixed`, `…test_file_links_expire_and_are_revoked_by_deletion`, `test_rag_graph::test_conversations_belong_to_their_session` |
| **D** Question flooding | Per-session, per-user, all-guests and per-IP limits (429 + `Retry-After`) | `test_operations::test_new_anonymous_sessions_share_one_public_budget`, `test_chat_api::test_rate_limited_callers_get_429_with_retry_after` |
| **I** Reading someone else's order by guessing its number | `POST /orders/lookup` needs the email or phone used at checkout. Every mismatch is the same 404 `ORDER_NOT_VERIFIED`, a missing order costs the same hash work, and 5 mismatches in 15 minutes lock both the caller and the order number (ADR-029). Order data comes from the orders API, never from the model | `test_orders_tickets::test_a_failed_check_never_says_whether_the_order_exists`, `…test_repeated_failures_lock_the_caller_and_the_order_number`, `…test_another_tenant_cannot_verify_this_tenants_order`, `…test_a_signed_in_customer_sees_only_their_own_orders` |
| **I** A chat answer showing someone else's order or repair | Records come from fixed queries for the caller only: their own account, or a record they verified in this conversation (grant, 30 min). Another person's conversation ID records nothing; a new chat starts over (ADR-031) | `test_account_answers::test_staff_and_other_customers_never_see_these_records`, `…test_a_grant_ends_when_it_expires_and_never_crosses_to_another_persons_chat` |
| **T** The model inventing a date, price or order in an account answer | Every reference and number in the answer must be in the caller's records, else a summary built from the records is used; cards come from the database (ADR-031) | `test_account_answers::test_an_answer_with_facts_not_in_the_records_is_replaced_by_the_records_summary`, `test_account::test_an_answer_is_used_only_if_its_numbers_and_references_are_in_the_records` |
| **I** A database leak exposing customer contacts | Only keyed HMAC-SHA256 hashes are stored; the key lives in the secret store, not the database (ADR-029) | `test_orders_tickets::test_no_contact_detail_is_stored_readably`, `test_contact` (4), `test_config::test_order_verification_needs_its_own_secret` |
| **E** A customer reading or filing support tickets | Ticket routes check the staff role on the server; a draft only reads the caller's own conversation | `test_orders_tickets::test_tickets_are_staff_only_and_drafts_only_from_your_own_conversation` |
| **I** Exports carrying personal data out | Managers and admins only; redacted like the dashboard; no conversations or ticket summaries; one tenant and range per file; every download audited (ADR-030) | `test_reviews_exports::test_exports_are_redacted_csv_and_audited`, `…test_exports_are_for_managers_and_admins_with_a_valid_range` |
| **T** A question that runs as a formula in a spreadsheet (CSV injection) | Cells starting with `=`, `+`, `-`, `@`, tab or CR are prefixed with `'` | `test_exports::test_cells_a_spreadsheet_would_run_are_quoted`, `test_reviews_exports::test_exports_are_redacted_csv_and_audited` |
| **I** One organization reaching another's data through any route | Tenant from the session on every route, RLS underneath; IDs from another tenant are 404 | `test_multi_org::test_another_organizations_ids_lead_nowhere`, `…test_another_organizations_lists_counts_and_exports_are_empty` |
| **I** Error details leaking hosts or SQL | Generic 500 with a request ID; `/docs` hidden outside development; HSTS | `test_health::test_component_down_returns_503_without_leaking_details`, `test_hygiene::test_deployed_environments_add_hsts_and_hide_the_docs` |

### Retrieval and the model (the AI boundary)

| Threat | Control | Proof |
| --- | --- | --- |
| **I** Employee-only text reaching a public answer | Audience and tenant filters built from the identity and applied **in the vector query and again in Python**, before any text reaches the model (ADR-004). The model can't be talked into revealing what it never received | G5: 0 / 96 access probes (`test_golden_gate`), `test_rag_graph::test_employee_only_content_never_reaches_the_public`, `test_chroma_index::test_other_tenants_chunks_are_never_returned` |
| **T** Prompt injection in the question | The question is data in a fixed template; it can't change filters, tools or audience | `test_adversarial::test_the_system_prompt_is_never_disclosed` |
| **T** Indirect injection inside a published document | Sources wrapped as labelled data; forged `</source>` tags escaped; no tools reachable from evidence; citations re-validated | `test_adversarial::test_instructions_inside_a_published_document_stay_data`, `test_rag::test_rendered_sources_are_labelled_escaped_and_budgeted` |
| **T** Fabricated citations or policies | Structured output; the citation validator rejects unknown IDs, quotes not in the source and URLs not in it; deterministic evidence gate → "Not in our documents" (ADR-016) | G3 0 / 36, G4 109 / 109; `test_rag::test_invalid_answers_are_rejected` |
| **T** Old or draft versions answering | Active version IDs resolved from Postgres per request (ADR-003) | G6 0 / 12 lifecycle scenarios |
| **I** Personal data sent to the provider | The models run on our own host (ADR-026): only the question as typed and the authorized chunks go to the model (no account data; a follow-up also carries the conversation's previous question, one turn, ADR-028), to the local `llm` service, on the private network. Traces and logs hold IDs and codes only (ADR-027) | `test_rag::test_the_local_model_is_called_on_its_own_server_without_a_key`, `test_config::test_local_models_need_their_server_url` |
| **D** Cost blow-up | One rewrite at most, ≤ 6,000 context tokens, 20 s model timeout, 30 s deadline; token usage and estimated cost per answer on Operations (ADR-021) | `test_rag::test_provider_retries_are_capped_at_one`, `test_observability::test_every_answer_records_the_trace_shape` |

### Voice service ↔ api

| Threat | Control | Proof |
| --- | --- | --- |
| **S** Someone else using the service route | Constant-time token check **and** caller IP inside `INTERNAL_API_ALLOWED_NETWORKS` (TCP peer; forged `X-Forwarded-For` ignored) | `test_operations::test_the_internal_api_only_answers_allowed_networks` |
| **E** Asking for employee content through voice | The `audience` field is ignored; the credential grants public only (ADR-017) | `test_api_spec::test_user_tokens_cannot_call_the_service_api`, voice contract tests |

### Uploads and the worker

| Threat | Control | Proof |
| --- | --- | --- |
| **T** Malware, macro documents, zip bombs, type spoofing | Allowlist + magic bytes, size and page caps, macro refusal, inflate limit, malware scan before storage (ADR-015) | `test_validation` (14), `test_documents_api::test_malware_is_blocked_and_audited_but_never_stored` |
| **T** Path traversal through file names | Storage keys are generated; names are sanitized for display only | `test_storage::test_keys_cannot_escape_the_storage_root` |
| **I** Secrets pasted into a document | Detected and redacted at ingestion, counted, never stored | `test_parsing::test_secrets_are_redacted_and_counted_but_never_recorded` |
| **D** Ingestion flooding | 5 active ingestions per tenant; leased jobs with capped retries | `test_ingestion::test_concurrent_ingestions_per_tenant_are_limited` |
| **R** "Who published this?" | Every admin change is an append-only audit event with actor and request ID | `test_operations::test_every_admin_change_is_audited`, `test_orders_tickets::test_staff_work_the_queue_and_every_change_is_audited` (tickets; order lookups are audited too) |

### api / worker ↔ data stores

| Threat | Control | Proof |
| --- | --- | --- |
| **I** A query bug exposing another tenant | Row-level security on every tenant table as a second layer; the app role sees nothing without a tenant (ADR-011) | `test_schema::test_cannot_read_another_tenants_row_even_by_id`, `…test_tenant_tables_return_nothing_without_a_tenant` |
| **I** Reaching Postgres/Chroma from outside | Data services only on the private `data` network; `web` can't resolve them; dev ports bound to 127.0.0.1 (compose.yaml) | Manual: `docs/demos/14-15-repository-release.md` |
| **I** Secrets or PII in logs | IDs and codes only; a redaction filter on every handler | `test_hygiene` (9 leak cases) |
| **I** Data kept too long | Retention purges every store (ADR-019) | `test_operations::test_retention_purges_every_store` |

## 4. Open and accepted risks

| Risk | Status | Plan |
| --- | --- | --- |
| `chromadb 1.5.9` advisories (incl. code injection on the server) | Accepted with mitigation (ADR-020): Chroma never reachable from outside the app network | Upgrade when a fixed release is ≥ 7 days old |
| Prompt injection against the local model | Red-teamed: `test_adversarial` 5 / 5 against it (docs/demos/19-red-team-local-model.txt), golden-v1 gates pass (evaluation-report.md §4a) | Re-run both after any model change |
| Model files from a third party | Pinned revision and SHA-256 per file (`LLM/models.json`); GGUF is data, not code; the model servers need no internet access once the files are downloaded | Re-check the hashes on download (`scripts/download_models.py --check`) |
| Encryption at rest, managed secrets, TLS termination | Config refuses to run staging/production without TLS to every store; volumes and bucket encryption are provisioning | §16 / M7 deployment |
| One service-wide Operations view per tenant manager | Fine for one tenant | Platform-operator role before tenants share a deployment (ADR-021) |
| No SSO / MFA for staff | App-managed passwords with lockout | P2 (SSO) |

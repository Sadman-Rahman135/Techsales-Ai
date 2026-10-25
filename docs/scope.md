# KnowledgeAI scope (blueprint §2, detailed)

Turns [IMPLEMENTATION_PLAN.md §2](IMPLEMENTATION_PLAN.md#2-scope-priorities-and-non-goals) into a buildable cut line:
what ships in the MVP, in which milestone, on which screen, and how each non-goal is enforced.
Personas and stories: [product-vision.md](product-vision.md). UI rules: [design-system/MASTER.md](../design-system/MASTER.md).

## 1. MVP definition (P0)

**The MVP is done when** a manager can upload and publish a TechCare document, a customer and an employee each get a cited
answer limited to what they may see, an uncovered question gets an honest "Not in our documents", and a voice test client gets
the same answer through the internal API. All of this needs passing tests, a manual test and a demo (§0).

| # | P0 item (§2.1) | Milestone | Story | Screen / surface | Verified by |
| --- | --- | --- | --- | --- | --- |
| 1 | User login + admin account | M1 | — | `/login` | Auth integration tests; seeded admin |
| 2 | One business; `organization_id` everywhere | M1 | — | (all) | Migration review; every query is tenant-scoped (§12.3) |
| 3 | Admin upload, list, status, preview, delete | M2 API · M4 UI | US-01, US-03 | `/admin/documents`, `/admin/documents/[id]` | API tests + Playwright upload flow |
| 4 | Background ingestion with retryable jobs | M2 | US-01 | Upload progress stepper | Failure-injection test: retry creates no duplicate chunks (§3) |
| 5 | Public/employee visibility, **server-side** | M3 · tests M5 | US-02, US-07 | Chat, assistant, internal API | Negative tests: public user never receives employee chunks |
| 6 | Searchable chat UI with conversation history | M3 API · M4 UI | US-02 | `/chat`, `/assistant` | Playwright chat flow; history for signed-in users only |
| 7 | LangGraph routing, retrieval, evidence check, bounded rewrite, answer | M3 | US-01, US-02, US-04 | — | Graph unit tests; rewrite capped at 1 |
| 8 | Clickable citations + insufficient-evidence fallback | M3 · M4 UI | US-02, US-04 | Answer block, source panel | Citation-validity test (100%); no-answer suite |
| 9 | Versioning with published/draft states | M2 records · M5 activation | US-03 | Version timeline, publish dialog | Lifecycle test: no old-version citations |
| 10 | Feedback buttons + basic unanswered dashboard | M5 | US-06 | Feedback row, `/admin/quality` | API counts match the dashboard for the same range |
| 11 | Logs, exceptions, metrics, tests, reproducible startup | M1 → all | — | — | Clean-machine README run (§3 Operability) |
| 12 | Internal knowledge API for the voice project | M6 | US-07 | `POST /internal/knowledge/answer` | Contract tests with a fake client (ADR-001) |

### Scope conflicts resolved here
The plan's §10 screen checklists ask for more than §2.1 P0. These decisions keep the MVP honest:

| §10 asks for | MVP (P0) | Deferred |
| --- | --- | --- |
| Employee filters by product, brand, category, type (§10.2) | **Product model** filter only (captured at upload) | Brand/category/type and full metadata search → P1 (§2.2) |
| Quality dashboard: intents, top documents, feedback summary (§10.4) | Totals, answered vs not-in-documents, unanswered list, ingestion failures | Top intents, most-cited documents, feedback trends: built (feedback trend in ADR-030) |
| Ticket handoff (§10.2) | "Contact support" link only | Ticket creation: built in P1 (ADR-029) |
| Conversation deletion (§10.1) | "Clear history" for signed-in users | Retention policies UI → P1 |

## 2. P1 and P2: when and how they appear

| Item | Priority | Target | UI treatment until built |
| --- | --- | --- | --- |
| Metadata search (model, brand, topic, department, language, date) | P1 | M5 | **Built** (ADR-028): assistant filters, documents list "More filters" |
| Hybrid lexical + vector retrieval with reranking | P1 | M5 (§8.5) | **Built** (ADR-028); no UI change, measured on the eval set |
| Customer order status via protected APIs | P1 | M6 | **Built** (ADR-029): verification card in the chat; voice keeps "Contact support" |
| Support tickets | P1 | M6 | **Built** (ADR-029): "Create ticket" in the assistant, staff queue at `/admin/tickets` |
| OCR for scanned documents | P1 | After M7, if sample files need it | Upload rejects scans with a clear message |
| Data export, admin review workflows | P1 | After M7 | **Built** (ADR-030): `/admin/reviews`, CSV exports on Quality and Documents |
| Evaluation automation + tracing dashboard | P1 | M5/M7 | Internal tooling only |
| Multiple organizations with tested isolation | P1 | M5 | **Tested** (ADR-030, `test_multi_org`); no org switcher in UI (one organization per account) |
| English/Bangla | P1 | After M7 | Fonts include Noto Sans Bengali fallback; copy uses i18n keys from day one |
| URL ingestion, multimodal, recommendations, agentic journeys, CRM, SSO | P2 | Not scheduled | Not shown |

**Rule:** unbuilt features never appear as disabled buttons or "coming soon" teasers in customer or employee screens.
A visible control must work. Explaining an absence is fine where users would otherwise be stuck, e.g. "We can't read scanned PDFs yet."

## 3. Non-goals as enforced guardrails (§2.4)

| Non-goal | Guardrail | Layer | Test |
| --- | --- | --- | --- |
| General internet search assistant | No web tools in the graph. Retrieval only from the org's Chroma collection; off-topic questions get `insufficient_evidence` | Graph, prompt | "What's the weather?" → Not in our documents |
| Training/fine-tuning a foundation model | Models are configured by ID only; no training pipeline in the repo | Architecture | — (review) |
| Autonomous policy creation | The LLM never writes to documents; only humans upload and publish. P2 drafts require manager approval | API permissions | No write endpoint is reachable by the graph |
| Warranty verdicts from PDFs alone | The answer prompt must not state eligibility when purchase facts matter; route to "Contact support" (content guide option C) | Prompt, eval set | "Is my laptop under warranty?" → explains the policy + Contact support, no verdict |
| Orders/identities in Chroma | Ingestion accepts only document uploads; orders live in Postgres behind `POST /orders/lookup` (ADR-029) | Ingestion, schema | Ingestion rejects non-document sources; Chroma metadata schema has no customer fields |
| LLM-executed SQL / unrestricted APIs | Tools are typed functions with fixed queries and scoped params; no DB credentials in the graph | Graph tools | Tool registry test: only allowlisted tools |

## 4. MVP screen inventory (P0)

Next.js App Router. Server Components by default; client components only for interactive parts (composer, source panel,
upload). Each route has a `loading.tsx` (skeletons) and an `error.tsx` (message + retry).

| Route | Persona | P0 elements | P1 additions |
| --- | --- | --- | --- |
| `/login` | All signed-in | Email + password, show/hide password, error below field, "Continue as guest" link to `/chat` | SSO (P2) |
| `/chat` | Alex, Jordan | Top bar (logo, AI badge, Contact support), empty state with suggested questions, composer, streamed answer block, citation chips, source panel, feedback, retry, error/rate-limit states | Order verification card |
| `/chat/[conversationId]` | Jordan | Same as above + history list + Clear history | — |
| `/assistant` and `/assistant/[id]` | Priya | App shell, role badge, product filter, Employee-only badges, Copy for customer | Brand/category/type filters, Create ticket |
| `/admin/documents` | Morgan, Casey | Table (cards below 768px), search, audience/status filters, Upload modal + progress stepper, empty state | Bulk actions (export: built, ADR-030) |
| `/admin/documents/[id]` | Morgan, Casey | Version timeline, Publish dialog, Preview tab, Chunks diagnostics tab, Activity (audit) tab, Archive/Delete | Review workflow |
| `/admin/quality` | Morgan | Time range, 4 KPI tiles, answered vs not-in-documents chart, unanswered table (redacted), ingestion failures + retry | Built: top intents, most-cited docs, feedback trend, export, links to `/admin/reviews` |
| `forbidden`, `not-found`, `error` | All | Plain explanation + a way back; 404 also used for hidden resources (§9.3) | — |

**Prototype:** [`design-system/prototypes/customer-chat.html`](../design-system/prototypes/customer-chat.html) is a static, clickable
P0 customer chat (empty, answered, not-in-documents and error states; source panel; light/dark). It is the visual reference for M4.

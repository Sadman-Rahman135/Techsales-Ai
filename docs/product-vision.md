# KnowledgeAI product vision and use case (blueprint §1, detailed)

This expands [IMPLEMENTATION_PLAN.md §1](IMPLEMENTATION_PLAN.md#1-product-vision-and-concrete-use-case) into
personas, testable user stories and journeys. UI rules: [design-system/MASTER.md](../design-system/MASTER.md).
Copy: [design-system/content-guide.md](../design-system/content-guide.md). All people, products and policies are fictional.

## 1. Problem

TechCare (fictional electronics retailer and repair center, 3 stores) keeps its knowledge in warranty PDFs, product manuals,
return policies, troubleshooting articles and employee guides. Today:

| Who | Pain | Business cost |
| --- | --- | --- |
| Customers | Can't find the current return/warranty rule; get different answers from different staff | Refunds granted in error, complaints, repeat contacts |
| Store employees | Search shared drives or ask a colleague while a customer waits at the counter | Slow service, inconsistent troubleshooting |
| Support managers | Can't see which questions go unanswered, or which documents are outdated | Gaps stay unfixed; old policies keep circulating |

**Baseline to measure later:** time to answer a policy question, repeat-contact rate, share of questions escalated.
These are tracked by the quality dashboard (§10.4); no current figures are claimed.

## 2. Solution: capabilities mapped to the system

| # | Capability (§1.2) | Where it lives | Priority | Proven by |
| --- | --- | --- | --- | --- |
| 1 | Authorized employees upload and manage knowledge | Admin document center, `POST /documents` | P0 | US-01, US-03 |
| 2 | Parse and index documents in Chroma | Ingestion worker (§7) | P0 | US-01 |
| 3 | LangGraph routes, retrieves *authorized* evidence, grades relevance, composes a grounded answer | `rag/graph.py` (§8) | P0 | US-01, US-02, US-04 |
| 4 | Exact sources shown so readers can verify | Citation chips + source panel | P0 | US-02 |
| 5 | Transactional APIs for dynamic facts (order status) | `GET /orders/{id}`, not Chroma | P1 | US-05 |
| 6 | Refuse to invent; escalate unanswerable cases | `insufficient_evidence` path + Contact support | P0 | US-04, US-06 |
| 7 | Secure answer API for a future voice agent | `POST /internal/knowledge/answer` (ADR-001) | P0 | US-07 |

**Product promise (one line):** *Ask TechCare anything in our documents, get a short answer with the page it came from, or an honest "not in our documents" and a person to talk to.*

## 3. Personas

### P-1 Anonymous customer: "Alex"
- **Goal:** a quick, trustworthy answer to "Can I return this?" or "Why is my printer printing faded pages?"
- **Context:** phone (≈70% of traffic assumed), often in the store car park or at home, one hand, may be frustrated.
- **Needs:** plain language, the source to show staff, a way to reach a person. Large, readable text; works on 375px screens.
- **Allowed:** public published documents only. No account data, no history beyond the current browser session.
- **Primary screen:** customer chat (§10.1). **Success signal:** answered without contacting support; rates the answer "Helpful".
- **Accessibility assumptions:** may use screen-reader, zoom or high-contrast settings. The public audience means WCAG AA is the minimum.

### P-2 Authenticated customer: "Jordan"
- **Goal:** "Where is my repair/order?" plus the same policy questions as Alex.
- **Context:** signed in on web; expects their history to be kept.
- **Allowed:** public documents + **their own** orders/tickets after verification (P1). Never other customers' data.
- **Primary screens:** customer chat with history; order verification card.
- **Success signal:** gets order status without phoning the store.

### P-3 Store employee: "Priya"
- **Goal:** troubleshoot a device or confirm an internal procedure in under a minute while the customer is at the counter.
- **Context:** shared counter desktop or tablet; frequent interruptions; knows product model numbers.
- **Needs:** filters by product/brand/type, internal sources clearly marked "Employee only", copyable customer-safe wording.
- **Allowed:** public + employee-audience documents for their organization. Cannot publish or delete.
- **Primary screen:** employee assistant (§10.2). **Success signal:** resolves without calling a colleague; low "Not helpful" rate.

### P-4 Support manager: "Morgan"
- **Goal:** keep knowledge current and close gaps the assistant can't answer.
- **Context:** desktop, weekly review sessions plus ad-hoc policy updates.
- **Needs:** upload with clear progress, explicit publish step with consequences shown, unanswered-question list, feedback summary.
- **Allowed:** upload, version, publish, archive; view analytics (questions redacted unless they hold `conversations:read`).
- **Primary screens:** admin document center (§10.3), quality dashboard (§10.4).
- **Success signal:** a published policy appears in answers and old versions stop being cited (US-03).

### P-5 Organization admin: "Casey"
- **Goal:** manage users, roles and settings for TechCare's tenant.
- **Allowed:** tenant-scoped admin operations, delete/purge documents, audit log. Cannot see other organizations.
- **Primary screens:** settings/users (P1), document center (delete flow).

### P-6 Service integration: "CallPilot voice agent" (machine)
- **Goal:** fetch a concise, cited, structured answer to speak to a caller.
- **Allowed:** `knowledge:answer` scope only, for one organization. Audience is computed from its credential, never trusted from the request (§9.2).
- **Interface:** `POST /api/v1/internal/knowledge/answer`. No UI. Contract-tested with a fake client (ADR-001).

### Permission matrix

| Capability | Alex | Jordan | Priya | Morgan | Casey | Voice |
| --- | :-: | :-: | :-: | :-: | :-: | :-: |
| Ask with public sources | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Employee-only sources | — | — | ✓ | ✓ | ✓ | only if credential grants it |
| Conversation history | session | ✓ own | ✓ own | ✓ own | ✓ own | per call |
| Own order status (P1) | — | ✓ after verify | lookup for customer | — | — | after caller verify |
| Upload / version / publish | — | — | — | ✓ | ✓ | — |
| Delete / purge | — | — | — | archive | ✓ | — |
| Analytics (redacted) | — | — | — | ✓ | ✓ | — |
| Read full conversations | — | — | — | with `conversations:read` | with `conversations:read` | — |

Enforced **server-side before retrieval** (§4.4); the UI only hides what the server already refuses.

## 4. User stories with acceptance criteria

Each story is done only with automated tests + a manual test + a screenshot/demo (§0). "Gate" links to §3 success criteria.

### US-01: Employee asks about a printer error (P0)
*As an employee, I upload a printer manual and can ask about specific error messages.*
- **Given** Morgan uploads `DemoPrinter-X2 User Manual.pdf` with audience Employee, **when** ingestion finishes, **then** the document shows `Published` (after publish) and the chunk diagnostics list page ranges.
- **Given** Priya filters by product `DemoPrinter-X2`, **when** asking "What does error E-04 mean?", **then** the answer cites the manual page that mentions E-04 and the status reads "Answered from 1 source".
- **Given** the manual is still Draft, **then** no answer cites it.
- Screens: admin upload, employee assistant · API: `POST /documents`, `POST /documents/{id}/publish`, `POST /chat/ask` · Gate: Retrieval, Citation validity.

### US-02: Customer sees the return policy and its source (P0)
*As a customer, I ask about returns and see the active policy, source and page/section.*
- **When** Alex asks "Can I return headphones after 20 days?", **then** the answer states the rule from the **published** return policy, with a chip `[1] Return policy · p. 2`.
- **When** Alex activates the chip, **then** the source panel opens at that page with the quoted passage highlighted; Esc returns focus to the chip.
- Citations only reference chunks that were retrieved **and** are authorized for the public audience.
- Screens: customer chat, source panel · API: `POST /chat/ask`, `GET /documents/{id}/preview` · Gate: Citation validity, Grounding.

### US-03: Manager publishes a revised policy (P0)
*As a manager, I publish a revised policy and old versions stop appearing in new responses.*
- **Given** Return policy v2 is published, **when** Morgan uploads v3 and confirms "Publish version 3", **then** new answers cite v3 only.
- The confirmation dialog states the consequence before publishing (content guide: Publish confirm).
- An audit entry records who published which version and when.
- Screens: document detail (version timeline) · API: `POST /documents/{id}/versions`, `POST /documents/{id}/publish` · Gate: Document lifecycle.

### US-04: Honest no-answer (P0)
*As a user, I ask a question not covered by documents and receive an honest no-answer response.*
- **When** Alex asks "Do you price-match Amazon?" and no published document covers it, **then** `answer_status = insufficient_evidence`, there are no citations, the status line reads "Not in our documents", and "Contact support" is the primary action.
- The response contains **no invented policy** (checked by the no-answer test suite).
- The question appears in Morgan's Unanswered questions list (US-06).
- Screens: customer chat · API: `POST /chat/ask` · Gate: Abstention.

### US-05: Order status only after verification (P1)
*As a customer, I ask for an order status and receive information only after authorization.*
- **When** Jordan asks "Where is my order?", **then** the assistant shows the verification card before any order data.
- Mismatched details produce the generic failure message (no hint whether the order exists).
- Order data comes from PostgreSQL via `GET /orders/{id}`; it never comes from Chroma or the LLM's memory.
- Screens: verification card · API: `GET /orders/{id}` · Gate: Access control.

### US-06: Admin sees what went wrong (P0 basic, P1 full)
*As an admin, I can see which questions resulted in bad retrieval, no answer, or human escalation.*
- The quality dashboard lists unanswered and "Not helpful" questions for the selected period, with PII redacted.
- Each row links to the cited (or missing) documents. A full conversation opens only with `conversations:read`.
- Counts match the API `GET /admin/analytics` for the same time range.
- Screens: quality dashboard · Gate: Operability.

### US-07: Voice client gets a structured cited answer (P0)
*As a voice-service client, I call a stable API and receive a concise, cited, structured response.*
- **Given** a service credential scoped to TechCare/public, **when** the client posts a question, **then** the response matches the versioned schema with `answer`, `speech_summary` (≤ 2 sentences), `citations` and `trace_id`.
- A request claiming `audience: employee` without a matching credential is answered as public (or 403), never as employee.
- Works with no Project 2 code present (ADR-001).
- API: `POST /internal/knowledge/answer` · Gate: Access control, Citation validity.

## 5. Key journeys

### J-1 Alex: "Can I return this?" (mobile, ~60 seconds)
1. Opens chat → empty state with disclosure + suggested chips → taps "Return policy".
2. Typing indicator → streamed answer → "Answered from 1 source" → chip `[1] Return policy · p. 2`.
3. Taps the chip → bottom sheet shows the highlighted rule → closes → taps the thumbs-up "Helpful" button.
   *Failure branch:* the question isn't covered → "Not in our documents" → Contact support.

### J-2 Priya: printer error at the counter (desktop, < 1 minute)
1. Selects product `DemoPrinter-X2` filter → asks "E-04 after toner change".
2. Answer cites the public manual and an **Employee only** service procedure.
3. Uses "Copy for customer" (public sentences only) → reads the steps aloud.
   *P1 branch:* not resolved → "Create ticket" with the auto-drafted summary.

### J-3 Morgan: policy update (desktop, weekly)
1. Document center → "Return policy" → "Upload new version" → progress stepper → Ready (Draft).
2. Reviews chunk diagnostics → "Publish version 3" → confirmation states the effect → toast "Version 3 published".
3. Opens the quality dashboard next week → unanswered questions → spots the "price match" gap → drafts a new article.

## 6. Assumptions and open questions (feed §19 / docs/architecture.md)

| Assumption | Why it matters | Decide in |
| --- | --- | --- |
| Customers may chat anonymously; history is kept only for signed-in users | Privacy + session design | §19 "anonymous or sign-in" |
| Customer traffic is mostly mobile | Mobile-first chat layout | Validate with analytics after launch |
| English first; Bangla is P1 | Fonts (Noto Sans Bengali fallback), embedding model choice | §2.2, §19 |
| Text PDFs only for MVP | Upload copy + parser choice | §19 "scans and tables" |
| Display in the tenant's configured IANA timezone (e.g. `America/Chicago`), UTC in the backend | Dashboard ranges stay correct across DST | §5.1 |
| Employees use shared counter devices | Short sessions, auto sign-out, no sensitive data cached | §11.1 |

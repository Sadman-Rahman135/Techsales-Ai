# P1 orders and support tickets (US-05, plan §2.2, §10.2, §12.1): evidence

Date: 2026-10-20. Decision record: ADR-029 in [architecture.md](../architecture.md). The browser checks ran against
the containers (`docker compose --profile app up -d --build`) on the local models (ADR-026), with demo data from
`python -m app.cli.seed` and `python -m app.cli.seed_orders`. All data is fictional.

## 1. What was built

| Plan item | What it does | Where |
| --- | --- | --- |
| §2.2 Order status (US-05) | An order question in the chat shows a verification card. The order number plus the email or phone used at checkout shows the order: status, dates, delivery, tracking, items, total | `app/api/orders.py`, `app/services/orders.py`, `components/chat/order-check.tsx` |
| §2.2 A customer's own orders | A signed-in customer linked to a customer record sees "Your recent orders" in the card, with nothing to type | `GET /orders/mine` |
| §2.2 / §10.2 Ticket handoff | In the employee assistant, "Create ticket" drafts a subject and summary from the conversation with the local model. The employee edits it and files it | `POST /tickets/draft`, `components/chat/ticket-panel.tsx` |
| §2.2 Ticket queue | Staff see a queue with counts per status, open a ticket, change status, priority or assignee, and add notes; every change is audited | `/admin/tickets`, `app/api/tickets.py`, `app/services/tickets.py` |
| §12.1 Synthetic products and orders | 51 products, 80 customers, 150 orders, plus three known demo orders | `app/demo/orders.py`, `python -m app.cli.seed_orders` |
| §12.3 The business tool returns only a verified caller's order | The graph never reads orders; the card does, through the verified lookup | `app/rag/graph.py` `business_tool` |

Demo orders (`DEMO_ORDERS`):
- **TC-100001:** shipped, belongs to the `alex.customer` account.
- **TC-100002:** ready for pickup, `jordan.lee@example.com`.
- **TC-100003:** delivered, `sam.rivera@example.com`.

## 2. Automated tests

| Test | Proves |
| --- | --- |
| `test_orders_tickets::test_an_order_question_shows_the_verification_card` | An order question returns `next_action = "verify_order"` and no order data |
| `…test_the_right_email_or_phone_shows_the_order` | Email (any case) or phone (any format) shows the order and its lines |
| `…test_a_failed_check_never_says_whether_the_order_exists` | A wrong number and a wrong contact give the same 404 and message |
| `…test_repeated_failures_lock_the_caller_and_the_order_number` | After 5 mismatches: 429 `ORDER_LOOKUP_LOCKED` with `Retry-After`, even with the right contact |
| `…test_another_tenant_cannot_verify_this_tenants_order` | Another organization's caller can't verify it with the right contact |
| `…test_a_signed_in_customer_sees_only_their_own_orders` | `GET /orders/mine` returns the linked customer's orders only |
| `…test_no_contact_detail_is_stored_readably` | No email or phone appears in any customer row, only 64-hex hashes |
| `…test_an_employee_drafts_reviews_and_files_a_ticket` | Draft from the employee's conversation, then the edited ticket is saved with its number and link |
| `…test_staff_work_the_queue_and_every_change_is_audited` | Status, priority, assignee and notes change and each writes an audit event; `resolved_at` follows the status |
| `…test_tickets_are_staff_only_and_drafts_only_from_your_own_conversation` | Customers get 403 and guests 401; drafting from someone else's conversation is 404; another tenant's staff see no tickets |
| `test_contact` (4 unit tests) | Normalization, keyed hashes, the development key derivation, order number spellings |
| `test_config::test_order_verification_needs_its_own_secret` | Staging and production refuse to start without a 32+ character `CUSTOMER_LOOKUP_SECRET` |
| `test_schema::test_every_tenant_table_has_forced_row_level_security` | The six new tables (and every other tenant table) force RLS |
| `test_rag_graph` order case | The chat gets `ORDER_VERIFY`; the voice channel keeps its handoff (frozen contract) |

## 3. Manual test (browser)

1. As a guest, ask "Where is my order TC-100002?". The card opens with the number filled in.
2. Enter a wrong email and select **Check order status**. The card says the details don't match, without saying
   which one ([p1-04](p1-04-order-verification-failed.png)).
3. Enter `Jordan.Lee@Example.com`. The order shows "Ready for pickup" with its items and total, marked "From
   TechCare's order system, not from the assistant" ([p1-05](p1-05-order-status-verified.png)).
4. Sign in as `alex.customer` and ask about an order. "Your recent orders" lists TC-100001; selecting it shows the
   order with nothing typed ([p1-09](p1-09-signed-in-customer-order.png)).
5. As staff (here the manager), ask the assistant a question, then select **Create ticket**. The local model
   drafts the subject and summary; review it, set the priority and order number, and select **Send to support**
   ([p1-06](p1-06-create-ticket-draft.png)).
6. Open the ticket from **Tickets**, set it to "In progress" and add a note. The ticket shows its status, priority,
   the reviewed summary (marked as drafted by the assistant) and the note ([p1-07](p1-07-ticket-detail.png)).
7. The queue in dark mode, with counts per status ([p1-08](p1-08-ticket-queue-dark.png)).

Checked in light and dark mode and at tablet width. One fix came out of it: the queue's ticket reference wrapped
at 768 px; it is now `nowrap`. Still to do before calling the screens done: the full MASTER.md checklist pass on the
card, the ticket panel and both ticket screens.

## 4. Known limits

- The lookup lock and rate limit are per API process, like the login lock. With several replicas each counts on
  its own (shared store: P1, with the rate limiter).
- Contacts are hashes, so staff can't see or export a customer's email or phone (by design, ADR-029).
- "Your recent orders" loads when the card opens; an order changed meanwhile shows after a reload.
- Resolved tickets have no retention rule yet (data-model §8).

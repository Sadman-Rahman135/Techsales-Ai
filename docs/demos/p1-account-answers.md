# Customers ask about their own orders and repairs (ADR-031): evidence

Date: 2026-10-23. Decision record: ADR-031 in [architecture.md](../architecture.md). Checked on the release containers
with the local models (ADR-026, GPU) and the fictional demo data (`python -m app.cli.seed_orders`).

## 1. What a customer can now ask

Any phrasing about their own orders and repairs. The answer comes from their records, with the matching cards
underneath. Real answers from the local model, signed in as the demo customer (`alex.customer`):

| Question | Answer (local model, checked against the records) | Cards |
| --- | --- | --- |
| what are my recent orders | You have one recent order: TC-100001. It was ordered on Friday 11 September 2026 and is currently on its way with express delivery… | TC-100001 |
| when can i pick up my products | You can pick up your products on Monday 19 October 2026 for repair RP-100001. The other repair, RP-100004, was collected on Sunday 20 September 2026. | RP-100001, RP-100004 |
| how many products do I have | You have 5 products in your order TC-100001. | TC-100001 |
| what was the faults with my product | The fault with your product, TechBook 14, was that it wouldn't charge and the battery icon showed a red cross. The fault with your DemoPrinter-X2 was that it printed faded pages. | the order and both repairs |
| Is my laptop ready? What was wrong with it? | Yes, your laptop repair is ready for pickup. The issue was a faulty USB-C charging port, and it has been replaced and tested. | RP-100001 |
| and how much did it cost? *(follow-up)* | The repair RP-100001 cost $0.00. The repair RP-100004 cost $25.00. | RP-100001, RP-100004 |
| total cost of the orders that are ready for pickup | There are no orders ready for pickup. The order TC-100001 is on its way, not ready for pickup… | TC-100001 |
| total spent on all my orders | The total spent on all your orders is $137.00. This amount is from order TC-100001. | TC-100001 |
| which items did I buy | You bought: 1 x 65 W USB-C charger ($49.00 each), 2 x Phone case ($25.00 each), and 2 x Smart Hub H1 stand, Black ($19.00 each)… | TC-100001 |
| What does error code E-04 mean on the DemoPrinter-X2? | Still answered from the documents, with a citation. | none |
| what is the restocking fee for opened items? | Still from the documents: 15%, with a citation (it mentions "items" but the documents answer it). | none |

Guests ask "Where is my order TC-100002?". They verify once in the card (order or repair number plus the email or
phone on it), then ask follow-ups in that chat ("When can I pick it up?") for 30 minutes.

Demo records: `DEMO_ORDERS` and `DEMO_REPAIRS` in `backend/app/demo/orders.py`:
- **Alex:** TC-100001, RP-100001 (ready for pickup), RP-100004 (collected).
- **Jordan** (`jordan.lee@example.com`): TC-100002 (pickup), RP-100002 (being diagnosed).
- **Sam** (`sam.rivera@example.com`): TC-100003, RP-100003.

### Test customers (`python -m app.cli.seed_test_customers`)

Three sign-in accounts with five orders each, one product per order. Logins are in `data/dev-credentials.txt`.

| Customer | Delivered in October 2026 | Expected after 15 November 2026 |
| --- | --- | --- |
| Maya Chen, `maya.chen@example.com` | TC-300101 TechBook 14 (8 Oct), TC-300102 Wireless Earbuds E3 (21 Oct) | TC-300103 DemoPrinter-X2 (18 Nov), TC-300105 100 W USB-C charger (20 Nov), TC-300104 Smart Hub H1 (24 Nov) |
| Omar Haddad, `omar.haddad@example.com` | TC-300201 DemoLaptop-Z1 (5 Oct), TC-300202 Toner cartridge X2 (collected 15 Oct) | TC-300203 TechBook 14 Pro (17 Nov), TC-300204 Wireless mouse (21 Nov), TC-300205 Laptop sleeve 14 in (26 Nov) |
| Lena Fischer, `lena.fischer@example.com` | TC-300301 DemoPrinter-X2 Pro (12 Oct), TC-300302 Phone case (22 Oct) | TC-300303 Wireless Earbuds E3 (16 Nov), TC-300304 Smart plug (19 Nov), TC-300305 TechBook 14 (30 Nov) |

Answers from the local model for these accounts, all correct and complete:

| Customer: question | Answer |
| --- | --- |
| Maya: what orders are still coming after November 15? | TC-300103 (DemoPrinter-X2) on Wednesday 18 November 2026, TC-300105 (100 W USB-C charger) on Friday 20 November 2026, TC-300104 (Smart Hub H1) on Tuesday 24 November 2026 |
| Maya: what did I receive this month? | TechBook 14 on Thursday 8 October 2026; Wireless Earbuds E3 on Wednesday 21 October 2026 |
| Maya: total cost of my orders that are not delivered yet | $387.00 (TC-300104, TC-300105, TC-300103) |
| Maya: when will my printer arrive? | Order TC-300103 (DemoPrinter-X2), Wednesday 18 November 2026 |
| Omar: how much have I spent in total? | $2,171.00 across all your orders |
| Lena: what is coming next month? | TC-300303 on 16 November, TC-300304 on 19 November, TC-300305 on 30 November |

## 2. Automated tests

| Test | Proves |
| --- | --- |
| `test_account_answers::test_a_signed_in_customer_gets_answers_from_their_own_records` | "my recent orders", "what was wrong with my laptop", "how many products" answer from the customer's records, with their cards |
| `…test_staff_and_other_customers_never_see_these_records` | An employee or a stranger gets the verification card, never someone's records |
| `…test_a_guest_verifies_an_order_and_asks_follow_ups_about_it` | Verify in the chat, then "When can I pick up my products?" and "and what is in it?" use that one order; a new chat starts over |
| `…test_a_grant_ends_when_it_expires_and_never_crosses_to_another_persons_chat` | Another person's conversation ID records nothing; an expired grant shows the card again |
| `…test_an_answer_with_facts_not_in_the_records_is_replaced_by_the_records_summary` | A model that invents "31 December 2099" and "$5.00" is replaced by the records summary |
| `…test_only_real_follow_ups_carry_the_earlier_question` | "how many products do I have" is its own question; "and how much did it cost?" carries the earlier one as context |
| `…test_record_questions_the_documents_cant_answer_fall_back_to_the_records` | "total cost of the orders that are ready for pickup" answers from the records; a guest without records still gets "Not in our documents"; a policy question the documents answer stays a documents answer |
| `…test_repairs_verify_like_orders_and_customers_see_their_own` | Repair lookup: generic 404 on mismatch, any spelling of the number, `/repairs/mine` |
| `test_test_customers` (2) | The three accounts match the request: five products each, two delivered in October, three expected after 15 November |
| `test_account` (unit tests) | The fact sheet (totals, pickup, diagnosis, the tenant's day), desk text kept as data, the grounding check, the summary, card choice, the offline model |
| `test_predicates` (16 new cases) | The new phrasings route to the records; "What's wrong with my printer?", "How do I collect points?" and similar stay document questions |
| `lib/orders.test.ts` | Order and repair numbers read the way people type them |

Routing check: none of the 354 evaluation questions (golden-v1, golden-v2, exploration, M3) changed route.

## 3. Manual test (browser)

1. Signed in as the demo customer, ask "Is my laptop ready? What was wrong with it?" The answer is labelled "From
   your TechCare account", with only the repair's card: fault, diagnosis, work done, store, ready date, cost
   ([p1-15](p1-15-account-answer-repair.jpg)).
2. Ask "when can i pick up my products". The answer names RP-100001 and RP-100004, and exactly those two cards
   follow ([p1-16](p1-16-account-answer-pickup-cards.jpg)).
3. The questions in §1 were asked in one conversation through the API (same containers, same model); the
   answers are copied above.

## 4. Known limits

- The grounding check covers numbers, dates and order/repair numbers. A wrong word with no number (e.g. a
  status) isn't caught by it; the cards from the database sit under every answer.
- Answers stay short (at most 3 sentences). Questions the records can't answer ("can my friend collect it?") get
  "I can't see that in your details" and the support link, not a policy answer.
- The demo data is fixed at 20 October 2026, so some "on its way" orders have expected dates in the past.

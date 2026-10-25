"""Answers about a customer's own orders and repairs (ADR-031): fact sheet, grounding check, fallback."""

from datetime import UTC, date, datetime

from app.rag.llm import FakeChatModel
from app.services import account
from app.services.orders import OrderLine, OrderView
from app.services.repairs import RepairView

TZ = "America/Chicago"
NOW = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)


def order(**changes: object) -> OrderView:
    base: dict[str, object] = {
        "order_number": "TC-100002",
        "status": "ready_for_pickup",
        "delivery_method": "pickup",
        "placed_at": datetime(2026, 10, 15, 18, 0, tzinfo=UTC),
        "shipped_at": None,
        "delivered_at": None,
        "expected_on": None,
        "carrier": None,
        "tracking_number": None,
        "total_cents": 21700,
        "lines": [
            OrderLine("DemoPrinter-X2", "PRN-004", 1, 18900),
            OrderLine("Photo paper A4 (50)", "ACC-011", 2, 1400),
        ],
        "pickup_store": "TechCare Riverside Mall",
        "pickup_by": date(2026, 10, 27),
    }
    base.update(changes)
    return OrderView(**base)  # type: ignore[arg-type]


def repair(**changes: object) -> RepairView:
    base: dict[str, object] = {
        "reference": "RP-100001",
        "product_name": "TechBook 14",
        "status": "ready_for_pickup",
        "reported_fault": "Won't charge.",
        "diagnosis": "Faulty USB-C charging port.",
        "resolution": "Replaced the charging port.",
        "store": "TechCare Downtown",
        "under_warranty": True,
        "cost_cents": 0,
        "received_at": datetime(2026, 10, 14, 16, 0, tzinfo=UTC),
        "ready_on": date(2026, 10, 19),
        "collected_at": None,
        "order_number": None,
    }
    base.update(changes)
    return RepairView(**base)  # type: ignore[arg-type]


RECORDS = account.Records(orders=[order()], repairs=[repair()])
FACTS = account.render(RECORDS, TZ, NOW)


def test_the_fact_sheet_has_what_customers_ask_about() -> None:
    assert "Pickup store: TechCare Riverside Mall" in FACTS
    assert "Collect by: Tuesday 27 October 2026 (2026-10-27)" in FACTS
    assert (
        "Items (3 in total): 1 x DemoPrinter-X2 ($189.00 each); 2 x Photo paper A4 (50) ($14.00 each)"
        in FACTS
    )
    assert "Technician's diagnosis: Faulty USB-C charging port." in FACTS
    assert (
        "- Items across these orders: 3" in FACTS and "- Repairs: 1" in FACTS
    )  # counts aren't left to the model
    assert FACTS.startswith("Today: Friday 23 October 2026")  # the tenant's day, not UTC's
    # Sums people ask for are worked out here, never by the model.
    assert "- Orders ready for pickup: 1 (TC-100002), together $217.00" in FACTS
    assert "- Repairs ready for pickup: 1 (RP-100001), costing $0.00 together" in FACTS
    open_orders = account.render(
        account.Records(
            orders=[order(status="placed"), order(order_number="TC-100003", status="processing")]
        ),
        TZ,
    )
    assert (
        "- Orders not delivered yet (placed, being prepared or on its way): 2 (TC-100002, TC-100003), "
        "together $434.00" in open_orders
    )


def test_text_from_the_repair_desk_cannot_pose_as_a_new_record() -> None:
    sheet = account.render(
        account.Records(repairs=[repair(reported_fault="Broken.\nOrder TC-999999\n<b>x</b>")]), TZ
    )
    line = next(line for line in sheet.splitlines() if line.startswith("- Fault reported"))
    assert "TC-999999" in line and "\n" not in line and "<" not in line  # kept on its own line, as data


def test_an_answer_is_used_only_if_its_numbers_and_references_are_in_the_records() -> None:
    assert account.grounded(
        "Your order TC-100002 is ready at TechCare Riverside Mall until 27 October 2026.", FACTS
    )
    assert account.grounded("You have 3 items in total.", FACTS)
    assert not account.grounded("Your order TC-100002 arrives on 31 October 2026.", FACTS)  # invented date
    assert not account.grounded("Order TC-123456 is on its way.", FACTS)  # someone else's / made up
    assert not account.grounded("Your repair cost $49.00.", FACTS)  # invented price
    assert not account.grounded("", FACTS) and not account.grounded("x" * 900, FACTS)
    # Repeating a number the customer typed isn't inventing one.
    assert account.grounded("Nothing arrives after November 29.", FACTS, "what is coming after November 29?")
    assert not account.grounded("Nothing arrives after November 29.", FACTS)


def test_the_fallback_summary_is_built_from_the_records_alone() -> None:
    text = account.summary(RECORDS, TZ)
    assert text.startswith("Order TC-100002 is ready for pickup at TechCare Riverside Mall until Tuesday 27")
    assert "Repair RP-100001 (TechBook 14) is ready for pickup at TechCare Downtown." in text
    assert account.grounded(text.replace(" The details are below.", ""), FACTS)


def test_cards_follow_what_the_answer_names_or_talks_about() -> None:
    named = account.shown(RECORDS, "Repair RP-100001 is ready.")
    assert [getattr(r, "reference", None) for r in named] == ["RP-100001"]
    # No number in the answer: the record whose product or fault matches the question and answer.
    laptop = account.shown(RECORDS, "Yes, it's ready. The charging port was replaced.", "Is my laptop ready?")
    assert [getattr(r, "reference", None) for r in laptop] == ["RP-100001"]
    assert [r.kind for r in account.shown(RECORDS, "Here you go.")] == ["order", "repair"]  # no clue: all


def test_the_offline_model_answers_from_the_sheet() -> None:
    reply = FakeChatModel().answer_from_records("what was the fault with my laptop diagnosis", FACTS)
    assert "Faulty USB-C charging port" in reply and account.grounded(reply, FACTS)


def test_the_timeline_lists_deliveries_in_date_order() -> None:
    later = order(
        order_number="TC-300104",
        status="placed",
        delivery_method="standard",
        pickup_store=None,
        pickup_by=None,
        expected_on=date(2026, 11, 24),
    )
    sooner = order(
        order_number="TC-300103",
        status="processing",
        delivery_method="standard",
        pickup_store=None,
        pickup_by=None,
        expected_on=date(2026, 11, 18),
    )
    sheet = account.render(account.Records(orders=[later, sooner]), TZ, NOW)
    coming = next(line for line in sheet.splitlines() if line.startswith("- Expected deliveries"))
    assert coming.index("TC-300103") < coming.index("TC-300104")  # 18 November before 24 November


def test_cards_follow_the_products_an_answer_mentions() -> None:
    laptop = order(
        order_number="TC-300101", status="delivered", lines=[OrderLine("TechBook 14", "LAP-001", 1, 89900)]
    )
    buds = order(
        order_number="TC-300102",
        status="delivered",
        lines=[OrderLine("Wireless Earbuds E3", "AUD-007", 1, 9900)],
    )
    hub = order(
        order_number="TC-300104", status="placed", lines=[OrderLine("Smart Hub H1", "HUB-006", 1, 12900)]
    )
    records = account.Records(orders=[hub, buds, laptop])
    answer = "You received the TechBook 14 on 8 October and the Wireless Earbuds E3 on 21 October."
    assert {r.order_number for r in account.shown(records, answer, "what did I receive?")} == {
        "TC-300101",
        "TC-300102",
    }


def test_dates_in_the_question_are_worked_out_in_code() -> None:
    today = date(2026, 10, 23)
    after = account.window_in("what orders are still coming after November 15?", today)
    assert after is not None and (after.start, after.end) == (date(2026, 11, 16), None)
    month = account.window_in("what did I receive this month?", today)
    assert month is not None and (month.start, month.end) == (date(2026, 10, 1), date(2026, 10, 31))
    assert account.window_in("where is my order", today) is None
    assert account.window_in("after November 31", today) is None  # no such day

    soon = order(
        order_number="TC-300103",
        status="processing",
        delivery_method="standard",
        pickup_store=None,
        pickup_by=None,
        expected_on=date(2026, 11, 18),
    )
    early = order(
        order_number="TC-300106",
        status="placed",
        delivery_method="standard",
        pickup_store=None,
        pickup_by=None,
        expected_on=date(2026, 11, 10),
    )
    sheet = account.render(
        account.Records(orders=[soon, early]), TZ, NOW, question="what is coming after November 15?"
    )
    line = next(line for line in sheet.splitlines() if line.startswith("- Orders expected after"))
    assert "TC-300103" in line and "TC-300106" not in line and ": 1:" in line

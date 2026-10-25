"""The hands-on test customers match what the owner asked for (app/demo/test_customers.py)."""

from datetime import date

from app.db.models import OrderStatus
from app.demo.test_customers import TEST_CUSTOMERS


def test_three_customers_with_five_products_two_done_this_month_three_after_mid_november() -> None:
    assert len(TEST_CUSTOMERS) == 3 and len({c.email for c in TEST_CUSTOMERS}) == 3
    for customer in TEST_CUSTOMERS:
        assert len(customer.orders) == 5 and len({o.product for o in customer.orders}) == 5
        done = [o for o in customer.orders if o.status is OrderStatus.DELIVERED]
        coming = [o for o in customer.orders if o.status is not OrderStatus.DELIVERED]
        assert len(done) == 2 and all(o.delivered and o.delivered.month == 10 for o in done)
        assert len(coming) == 3 and all(o.expected and o.expected > date(2026, 11, 15) for o in coming)
        assert all(o.delivered is None for o in coming)


def test_order_numbers_are_unique_and_valid() -> None:
    numbers = [o.number for c in TEST_CUSTOMERS for o in c.orders]
    assert len(numbers) == len(set(numbers)) == 15
    assert all(n.startswith("TC-3") and len(n) == 9 for n in numbers)

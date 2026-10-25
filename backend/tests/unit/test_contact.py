"""Contact details as keyed hashes (ADR-029)."""

from app.core import contact
from app.services.orders import normalize_number
from tests.unit.test_config import make

KEY = b"k" * 32


def test_an_email_matches_however_it_is_typed_and_a_phone_however_it_is_written() -> None:
    stored = contact.contact_hash(KEY, "email", "alex@example.com")
    phone = contact.contact_hash(KEY, "phone", "(555) 010-0001")
    assert contact.matches(KEY, "  Alex@Example.COM ", stored, phone)
    assert contact.matches(KEY, "+1 555 010 0001", stored, phone)  # the +1 country code is optional
    assert contact.matches(KEY, "555.010.0001", stored, phone)
    assert not contact.matches(KEY, "555 010 0002", stored, phone)
    assert not contact.matches(KEY, "alex@example.org", stored, phone)
    assert not contact.matches(KEY, "555 010 0001", stored, None)  # no phone on file


def test_hashes_depend_on_the_key_and_the_kind() -> None:
    assert contact.contact_hash(KEY, "email", "a@b.c") != contact.contact_hash(b"x" * 32, "email", "a@b.c")
    assert contact.contact_hash(KEY, "phone", "5550100001") != contact.contact_hash(
        KEY, "email", "5550100001"
    )
    assert len(contact.contact_hash(KEY, "email", "a@b.c")) == 64


def test_the_key_is_its_own_secret_or_derived_for_development() -> None:
    derived = contact.contact_key(make())
    assert derived == contact.contact_key(make()) and len(derived) == 32
    assert contact.contact_key(make(customer_lookup_secret="s" * 32)) == b"s" * 32


def test_order_numbers_are_read_the_way_people_type_them() -> None:
    assert normalize_number("tc 123456") == normalize_number("TC123456") == "TC-123456"
    assert normalize_number("TC-12345") == "TC-12345"  # not six digits: left for the lookup to refuse

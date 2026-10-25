"""Time rules (blueprint §5.1): the backend stores and compares UTC-aware datetimes only;
conversion to the tenant's timezone happens at the display edge."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_tenant_time(value: datetime, tenant_timezone: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime; store and pass UTC-aware values only")
    return value.astimezone(ZoneInfo(tenant_timezone))

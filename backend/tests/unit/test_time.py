"""Blueprint §5.1: UTC timestamps in the backend; display in the tenant timezone."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.time import to_tenant_time, utcnow


def test_utcnow_is_timezone_aware_utc() -> None:
    now = utcnow()
    assert now.tzinfo is UTC
    assert now.utcoffset() == timedelta(0)


def test_converts_to_tenant_time_across_dst() -> None:
    summer = datetime(2026, 7, 1, 17, 0, tzinfo=UTC)
    winter = datetime(2026, 12, 1, 17, 0, tzinfo=UTC)
    assert to_tenant_time(summer, "America/Chicago").hour == 12  # CDT, UTC-5
    assert to_tenant_time(winter, "America/Chicago").hour == 11  # CST, UTC-6


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_tenant_time(datetime(2026, 7, 1, 17, 0), "America/Chicago")

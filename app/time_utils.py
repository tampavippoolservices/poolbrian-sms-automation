from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(UTC)


def next_day_email_at(
    starting_at: datetime,
    *,
    timezone_name: str,
    hour: int,
) -> datetime:
    zone = ZoneInfo(timezone_name)
    local = starting_at.astimezone(zone)
    send_date = local.date() + timedelta(days=1)
    return datetime.combine(send_date, time(hour=hour), zone).astimezone(UTC)


def first_saturday_after(
    first_email_at: datetime,
    *,
    timezone_name: str,
    hour: int,
) -> datetime:
    zone = ZoneInfo(timezone_name)
    local_date = first_email_at.astimezone(zone).date()
    days_until_saturday = (5 - local_date.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    saturday = local_date + timedelta(days=days_until_saturday)
    return datetime.combine(saturday, time(hour=hour), zone).astimezone(UTC)


def within_local_hours(now: datetime, timezone_name: str, start_hour: int, end_hour: int) -> bool:
    local_hour = now.astimezone(ZoneInfo(timezone_name)).hour
    return start_hour <= local_hour < end_hour

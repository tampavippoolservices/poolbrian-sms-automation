from __future__ import annotations

from datetime import datetime, timedelta

from app.time_utils import first_saturday_after, next_day_email_at


def review_message_schedule(
    now: datetime,
    *,
    timezone_name: str,
    sms_delay_hours: int,
    email_hour: int,
) -> dict[str, datetime]:
    initial_sms = now + timedelta(hours=sms_delay_hours)
    next_day_email = next_day_email_at(
        now,
        timezone_name=timezone_name,
        hour=email_hour,
    )
    saturday_email = first_saturday_after(
        next_day_email,
        timezone_name=timezone_name,
        hour=email_hour,
    )
    return {
        "initial_review_sms": initial_sms,
        "next_day_review_email": next_day_email,
        "saturday_review_email": saturday_email,
    }


def normalized_person_name(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.casefold().split())


def name_match_score(customer_name: str | None, reviewer_name: str | None) -> float:
    customer = normalized_person_name(customer_name)
    reviewer = normalized_person_name(reviewer_name)
    if not customer or not reviewer:
        return 0.0
    if customer == reviewer:
        return 1.0
    customer_parts = set(customer.split())
    reviewer_parts = set(reviewer.split())
    if not customer_parts or not reviewer_parts:
        return 0.0
    overlap = len(customer_parts & reviewer_parts)
    return overlap / max(len(customer_parts), len(reviewer_parts))

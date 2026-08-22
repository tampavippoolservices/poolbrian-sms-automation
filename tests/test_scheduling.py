from datetime import UTC, datetime, timedelta

from app.domain.reviews import name_match_score, review_message_schedule
from app.time_utils import first_saturday_after, next_day_email_at, within_local_hours


def test_next_day_email_uses_business_timezone_across_dst() -> None:
    completed = datetime(2026, 3, 7, 23, 30, tzinfo=UTC)
    scheduled = next_day_email_at(
        completed,
        timezone_name="America/New_York",
        hour=10,
    )
    assert scheduled == datetime(2026, 3, 8, 14, 0, tzinfo=UTC)


def test_first_saturday_is_strictly_after_first_email() -> None:
    saturday_email = datetime(2026, 8, 22, 14, 0, tzinfo=UTC)
    assert first_saturday_after(
        saturday_email,
        timezone_name="America/New_York",
        hour=10,
    ) == datetime(2026, 8, 29, 14, 0, tzinfo=UTC)


def test_review_schedule_matches_business_rules() -> None:
    completed = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)
    schedule = review_message_schedule(
        completed,
        timezone_name="America/New_York",
        sms_delay_hours=3,
        email_hour=10,
    )
    assert schedule["initial_review_sms"] == completed + timedelta(hours=3)
    assert schedule["next_day_review_email"] == datetime(2026, 8, 21, 14, 0, tzinfo=UTC)
    assert schedule["saturday_review_email"] == datetime(2026, 8, 22, 14, 0, tzinfo=UTC)


def test_name_match_is_conservative() -> None:
    assert name_match_score("Javier Tamayo", "javier tamayo") == 1.0
    assert name_match_score("Javier Tamayo", "Javier") == 0.5
    assert name_match_score("", "Javier") == 0.0


def test_processing_window_is_end_exclusive() -> None:
    assert within_local_hours(datetime(2026, 8, 20, 10, 0, tzinfo=UTC), "America/New_York", 6, 19)
    assert not within_local_hours(
        datetime(2026, 8, 20, 23, 0, tzinfo=UTC), "America/New_York", 6, 19
    )

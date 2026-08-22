from __future__ import annotations

from sqlalchemy import text

from app.db import transaction


def get_state(key: str) -> str | None:
    with transaction() as connection:
        value = connection.execute(
            text("SELECT value FROM automation_state WHERE key = :key"),
            {"key": key},
        ).scalar_one_or_none()
        return str(value) if value is not None else None


def set_state(key: str, value: str) -> None:
    with transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO automation_state (key, value)
                VALUES (:key, :value)
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """
            ),
            {"key": key, "value": value},
        )


def delete_state(key: str) -> None:
    with transaction() as connection:
        connection.execute(text("DELETE FROM automation_state WHERE key = :key"), {"key": key})


def baseline_completed_jobs(jobs: list[dict]) -> int:
    count = 0
    with transaction() as connection:
        for job in jobs:
            record_id = _positive_int(job.get("RecordID"))
            customer_id = _positive_int(job.get("CustomerId"))
            if record_id is None:
                continue
            result = connection.execute(
                text(
                    """
                    INSERT INTO processed_jobs (record_id, customer_id, status)
                    VALUES (:record_id, :customer_id, 'baseline')
                    ON CONFLICT (record_id) DO NOTHING
                    """
                ),
                {"record_id": record_id, "customer_id": customer_id},
            )
            count += int(result.rowcount or 0)
        connection.execute(
            text(
                """
                INSERT INTO automation_state (key, value)
                VALUES ('completed_service_baseline_v2', 'complete')
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """
            )
        )
    return count


def _positive_int(value) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None

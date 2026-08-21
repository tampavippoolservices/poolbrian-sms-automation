from database import get_db_connection

def normalize_communication_destination(
    channel,
    destination
):
    value = (destination or "").strip()

    if not value:
        return ""

    if channel == "email":
        return value.lower()

    if channel == "sms":
        digits = "".join(
            character
            for character in value
            if character.isdigit()
        )

        if len(digits) == 10:
            return f"+1{digits}"

        if (
            len(digits) == 11
            and digits.startswith("1")
        ):
            return f"+{digits}"

        if value.startswith("+") and digits:
            return f"+{digits}"

        return digits

    return value


def communication_is_suppressed(
    channel,
    destination
):
    normalized_destination = (
        normalize_communication_destination(
            channel,
            destination
        )
    )

    if not normalized_destination:
        return False

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT is_suppressed
                FROM communication_suppressions
                WHERE channel = %s
                AND destination = %s
            """, (
                channel,
                normalized_destination
            ))

            row = cur.fetchone()

    return bool(row and row[0])


def save_communication_preference(
    channel,
    destination,
    is_suppressed,
    reason,
    source
):
    if channel not in ("sms", "email"):
        raise ValueError(
            "Unsupported communication channel"
        )

    normalized_destination = (
        normalize_communication_destination(
            channel,
            destination
        )
    )

    if not normalized_destination:
        return False

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO communication_suppressions (
                    channel,
                    destination,
                    is_suppressed,
                    reason,
                    source,
                    suppressed_at,
                    resumed_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    CASE
                        WHEN %s THEN NOW()
                        ELSE NULL
                    END,
                    CASE
                        WHEN %s THEN NULL
                        ELSE NOW()
                    END
                )
                ON CONFLICT (channel, destination)
                DO UPDATE SET
                    is_suppressed =
                        EXCLUDED.is_suppressed,
                    reason = EXCLUDED.reason,
                    source = EXCLUDED.source,
                    suppressed_at = CASE
                        WHEN EXCLUDED.is_suppressed
                            THEN NOW()
                        ELSE
                            communication_suppressions
                            .suppressed_at
                    END,
                    resumed_at = CASE
                        WHEN EXCLUDED.is_suppressed
                            THEN NULL
                        ELSE NOW()
                    END,
                    updated_at = NOW()
            """, (
                channel,
                normalized_destination,
                is_suppressed,
                reason,
                source,
                is_suppressed,
                is_suppressed
            ))

        conn.commit()

    return True

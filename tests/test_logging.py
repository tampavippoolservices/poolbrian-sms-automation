import logging

from app.logging_config import JsonFormatter, sanitize_log_text


def test_sensitive_values_are_masked_in_log_messages_and_exceptions() -> None:
    raw = (
        "customer@example.com +1 (813) 555-1212 "
        "Authorization: Bearer abc.def refresh_token=super-secret"
    )
    sanitized = sanitize_log_text(raw)
    assert "customer@example.com" not in sanitized
    assert sanitized.count("1212") == 1
    assert "813" not in sanitized
    assert "abc.def" not in sanitized
    assert "super-secret" not in sanitized

    try:
        raise RuntimeError(raw)
    except RuntimeError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed for customer@example.com",
            args=(),
            exc_info=__import__("sys").exc_info(),
        )
    rendered = JsonFormatter().format(record)
    assert "customer@example.com" not in rendered
    assert "super-secret" not in rendered

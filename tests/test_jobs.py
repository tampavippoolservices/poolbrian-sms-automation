from datetime import timedelta

from app.domain.jobs import is_retryable_http_status, provider_status_rank, retry_delay


def test_status_rank_never_regresses_terminal_delivery() -> None:
    assert provider_status_rank("delivered") > provider_status_rank("sent")
    assert provider_status_rank("failed") == provider_status_rank("delivered")
    assert provider_status_rank("unknown") == 0


def test_retry_delay_is_exponential_and_capped() -> None:
    assert retry_delay(1) == timedelta(seconds=30)
    assert retry_delay(2) == timedelta(seconds=60)
    assert retry_delay(100) == timedelta(hours=1)


def test_retryable_http_statuses() -> None:
    assert is_retryable_http_status(429)
    assert is_retryable_http_status(503)
    assert not is_retryable_http_status(400)

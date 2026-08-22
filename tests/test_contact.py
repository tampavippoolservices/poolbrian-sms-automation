import pytest

from app.domain.contact import (
    InvalidContact,
    masked_destination,
    normalize_email,
    normalize_us_phone,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("813-555-1212", "+18135551212"),
        ("+1 (813) 555-1212", "+18135551212"),
        ("1.813.555.1212", "+18135551212"),
    ],
)
def test_normalize_us_phone(raw: str, expected: str) -> None:
    assert normalize_us_phone(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "5551212", "+441234567890", "28135551212"])
def test_reject_invalid_phone(raw: str | None) -> None:
    with pytest.raises(InvalidContact):
        normalize_us_phone(raw)


def test_email_normalization_and_masking() -> None:
    assert normalize_email(" Javier@Example.COM ") == "javier@example.com"
    assert masked_destination("javier@example.com") == "j***@example.com"
    assert masked_destination("+18135551212") == "***-***-1212"


@pytest.mark.parametrize("raw", [None, "javier", "a@@example.com", "a@localhost", "a @x.com"])
def test_reject_invalid_email(raw: str | None) -> None:
    with pytest.raises(InvalidContact):
        normalize_email(raw)

from app.domain.bounces import extract_permanent_bounce


def test_extracts_unique_known_permanent_bounce() -> None:
    message = {
        "subject": "Undeliverable: pool service",
        "bodyPreview": "550 5.1.1 User unknown customer@example.com",
    }
    assert extract_permanent_bounce(message, {"customer@example.com"}) == "customer@example.com"


def test_does_not_suppress_temporary_or_ambiguous_bounce() -> None:
    temporary = {
        "subject": "Delivery is delayed",
        "bodyPreview": "We will keep trying customer@example.com",
    }
    assert extract_permanent_bounce(temporary, {"customer@example.com"}) is None
    ambiguous = {
        "subject": "Undeliverable",
        "bodyPreview": "550 5.1 customer@example.com and other@example.com",
    }
    assert (
        extract_permanent_bounce(ambiguous, {"customer@example.com", "other@example.com"}) is None
    )

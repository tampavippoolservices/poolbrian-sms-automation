from __future__ import annotations

import re


class InvalidContact(ValueError):
    pass


def normalize_us_phone(value: str | None) -> str:
    if not value:
        raise InvalidContact("phone number is missing")
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) != 11 or not digits.startswith("1"):
        raise InvalidContact("phone number must be a US/Canada 10-digit number")
    return "+" + digits


def normalize_email(value: str | None) -> str:
    if not value:
        raise InvalidContact("email address is missing")
    normalized = value.strip().casefold()
    if len(normalized) > 254 or normalized.count("@") != 1:
        raise InvalidContact("email address is invalid")
    local, domain = normalized.rsplit("@", 1)
    if (
        not local
        or not domain
        or "." not in domain
        or any(character.isspace() for character in normalized)
    ):
        raise InvalidContact("email address is invalid")
    return normalized


def masked_destination(value: str | None) -> str:
    if not value:
        return "Not recorded"
    if "@" in value:
        local, domain = value.rsplit("@", 1)
        visible = local[:1] if local else ""
        return f"{visible}***@{domain}"
    digits = re.sub(r"\D", "", value)
    return f"***-***-{digits[-4:]}" if len(digits) >= 4 else "***"

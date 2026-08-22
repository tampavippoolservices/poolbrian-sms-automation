from __future__ import annotations

import re
from typing import Any

_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PERMANENT_MARKERS = (
    "undeliverable",
    "couldn't be delivered",
    "could not be delivered",
    "delivery has failed",
    "recipient address rejected",
    "user unknown",
    "mailbox unavailable",
    "550 5.",
)
_TEMPORARY_MARKERS = ("delivery delayed", "delivery is delayed", "will keep trying", "temporary")


def extract_permanent_bounce(message: dict[str, Any], known_destinations: set[str]) -> str | None:
    subject = str(message.get("subject") or "")
    preview = str(message.get("bodyPreview") or "")
    content = f"{subject}\n{preview}".casefold()
    if any(marker in content for marker in _TEMPORARY_MARKERS):
        return None
    if not any(marker in content for marker in _PERMANENT_MARKERS):
        return None
    candidates = {match.group(0).casefold() for match in _EMAIL_PATTERN.finditer(content)}
    matched = sorted(candidates & known_destinations)
    return matched[0] if len(matched) == 1 else None

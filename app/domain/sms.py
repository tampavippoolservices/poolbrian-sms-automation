from __future__ import annotations

_GSM_BASIC = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./"
    "0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM_EXTENDED = frozenset("^{}\\[~]|€")


def sms_segment_count(message: str) -> int:
    if not message:
        return 0
    gsm_units = 0
    for character in message:
        if character in _GSM_BASIC:
            gsm_units += 1
        elif character in _GSM_EXTENDED:
            gsm_units += 2
        else:
            return _segments(len(message), single=70, multipart=67)
    return _segments(gsm_units, single=160, multipart=153)


def _segments(length: int, *, single: int, multipart: int) -> int:
    if length <= single:
        return 1
    return (length + multipart - 1) // multipart

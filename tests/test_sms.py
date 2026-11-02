from app.domain.sms import sms_segment_count


def test_sms_segment_count_handles_gsm_and_unicode() -> None:
    assert sms_segment_count("A" * 160) == 1
    assert sms_segment_count("A" * 161) == 2
    assert sms_segment_count("^" * 81) == 2
    assert sms_segment_count("Pool update ☀") == 1
    assert sms_segment_count("☀" * 71) == 2

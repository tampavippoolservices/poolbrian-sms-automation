import pytest

from app.messages import render_message


def test_sms_review_link_is_on_separate_line() -> None:
    rendered = render_message(
        template_key="initial_review_sms",
        data={"customer_name": "Javier", "review_token": "secure-token"},
        public_base_url="https://example.com",
    )
    assert "\n\nhttps://example.com/review/secure-token\n\n" in rendered.text
    assert "STOP" in rendered.text


def test_email_has_plain_html_and_unsubscribe_versions() -> None:
    rendered = render_message(
        template_key="next_day_review_email",
        data={"customer_name": "Javier", "review_token": "secure-token"},
        public_base_url="https://example.com/",
        unsubscribe_token="unsubscribe-token",
    )
    assert rendered.subject == "How was your recent pool service?"
    assert "https://example.com/review/secure-token" in rendered.text
    assert "https://example.com/unsubscribe/unsubscribe-token" in rendered.text
    assert rendered.html and "Leave an honest Google review" in rendered.html


def test_final_email_is_identified_as_final() -> None:
    rendered = render_message(
        template_key="saturday_review_email",
        data={"review_token": "secure-token"},
        public_base_url="https://example.com",
        unsubscribe_token="unsubscribe-token",
    )
    assert rendered.subject and rendered.subject.startswith("Final follow-up")


def test_email_requires_unsubscribe_token() -> None:
    with pytest.raises(ValueError, match="unsubscribe"):
        render_message(
            template_key="next_day_review_email",
            data={"review_token": "secure-token"},
            public_base_url="https://example.com",
        )


def test_website_lead_sms_contains_callback_details() -> None:
    rendered = render_message(
        template_key="admin_website_lead_sms",
        data={
            "lead_id": 42,
            "mode": "callback",
            "name": "Taylor Smith",
            "phone": "+18135550199",
            "zip": "33609",
            "service": "Weekly pool service",
        },
        public_base_url="https://example.com",
    )
    assert "New website lead #42" in rendered.text
    assert "+18135550199" in rendered.text
    assert "Callback" in rendered.text


def test_website_lead_email_escapes_customer_content() -> None:
    rendered = render_message(
        template_key="admin_website_lead_email",
        data={
            "lead_id": 42,
            "mode": "schedule",
            "name": "<script>alert(1)</script>",
            "phone": "+18135550199",
            "zip": "33609",
            "service": "Equipment service",
            "notes": "Pump <b>noise</b>",
            "sms_consent": True,
        },
        public_base_url="https://example.com",
    )
    assert rendered.subject and "New Tampa VIP website lead" in rendered.subject
    assert rendered.html and "&lt;script&gt;" in rendered.html
    assert "<script>" not in rendered.html
    assert "Pump &lt;b&gt;noise&lt;/b&gt;" in rendered.html

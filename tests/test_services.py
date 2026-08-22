import base64
from email import policy
from email.parser import BytesParser

import pytest
import responses

from app.services.google import GoogleBusinessClient
from app.services.microsoft import MicrosoftApiError, MicrosoftGraphClient
from app.services.twilio import SmsSendError


@responses.activate
def test_google_review_pagination(monkeypatch) -> None:
    client = GoogleBusinessClient()
    monkeypatch.setattr(client, "access_token", lambda: "access-token")
    url = "https://mybusiness.googleapis.com/v4/accounts/1/locations/2/reviews"
    responses.get(
        url,
        json={"reviews": [{"reviewId": "one"}], "nextPageToken": "next"},
        status=200,
    )
    responses.get(url, json={"reviews": [{"reviewId": "two"}]}, status=200)
    assert [row["reviewId"] for row in client.iter_reviews("accounts/1", "locations/2")] == [
        "one",
        "two",
    ]
    assert responses.calls[0].request.params["pageSize"] == "50"
    assert responses.calls[1].request.params["pageToken"] == "next"


@responses.activate
def test_microsoft_sends_multipart_draft_then_send(monkeypatch) -> None:
    monkeypatch.setenv("OUTLOOK_SENDER_ADDRESS", "office@example.com")
    client = MicrosoftGraphClient()
    monkeypatch.setattr(client, "access_token", lambda: "access-token")
    draft_url = "https://graph.microsoft.com/v1.0/users/office%40example.com/messages"
    send_url = "https://graph.microsoft.com/v1.0/users/office%40example.com/messages/draft-1/send"
    responses.post(draft_url, json={"id": "draft-1"}, status=201)
    responses.post(send_url, status=202)

    result = client.send_email(
        destination="customer@example.com",
        subject="Pool service",
        text_body="Plain version",
        html_body="<p>HTML version</p>",
        message_job_id=42,
    )

    assert result.provider_status == "accepted"
    encoded = responses.calls[0].request.body
    assert isinstance(encoded, (str, bytes))
    message = BytesParser(policy=policy.default).parsebytes(base64.b64decode(encoded))
    assert message["X-Tampa-VIP-Job-ID"] == "42"
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "Plain version"
    assert "HTML version" in message.get_body(preferencelist=("html",)).get_content()


@responses.activate
def test_microsoft_ambiguous_send_failure_is_not_retryable(monkeypatch) -> None:
    monkeypatch.setenv("OUTLOOK_SENDER_ADDRESS", "office@example.com")
    client = MicrosoftGraphClient()
    monkeypatch.setattr(client, "access_token", lambda: "access-token")
    draft_url = "https://graph.microsoft.com/v1.0/users/office%40example.com/messages"
    send_url = "https://graph.microsoft.com/v1.0/users/office%40example.com/messages/draft-1/send"
    responses.post(draft_url, json={"id": "draft-1"}, status=201)
    responses.post(send_url, json={"error": {"code": "serverError"}}, status=503)

    with pytest.raises(MicrosoftApiError) as captured:
        client.send_email(
            destination="customer@example.com",
            subject="Pool service",
            text_body="Plain version",
            html_body="<p>HTML version</p>",
            message_job_id=42,
        )

    assert captured.value.uncertain is True
    assert captured.value.retryable is False


def test_uncertain_sms_error_is_explicit() -> None:
    error = SmsSendError("unknown", code="network_error", uncertain=True)
    assert error.uncertain is True
    assert error.retryable is False

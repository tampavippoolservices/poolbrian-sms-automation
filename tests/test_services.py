import json

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
def test_microsoft_sends_structured_draft_then_send(monkeypatch) -> None:
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
    payload = json.loads(encoded)
    assert payload["subject"] == "Pool service"
    assert payload["body"] == {
        "contentType": "HTML",
        "content": "<p>HTML version</p>",
    }
    assert payload["toRecipients"] == [{"emailAddress": {"address": "customer@example.com"}}]
    assert payload["internetMessageHeaders"] == [
        {
            "name": "X-Tampa-VIP-Job-ID",
            "value": "42",
        }
    ]


@responses.activate
def test_microsoft_structured_draft_preserves_long_action_urls(monkeypatch) -> None:
    monkeypatch.setenv("OUTLOOK_SENDER_ADDRESS", "office@example.com")
    client = MicrosoftGraphClient()
    monkeypatch.setattr(client, "access_token", lambda: "access-token")
    draft_url = "https://graph.microsoft.com/v1.0/users/office%40example.com/messages"
    send_url = "https://graph.microsoft.com/v1.0/users/office%40example.com/messages/draft-1/send"
    responses.post(draft_url, json={"id": "draft-1"}, status=201)
    responses.post(send_url, status=202)
    review_url = "https://example.com/review/" + ("r" * 64)
    unsubscribe_url = "https://example.com/unsubscribe/" + ("u" * 64)
    html_body = f'<a href="{review_url}">Review</a><a href="{unsubscribe_url}">Unsubscribe</a>'

    client.send_email(
        destination="customer@example.com",
        subject="Pool service",
        text_body="Plain version",
        html_body=html_body,
        message_job_id=42,
    )

    encoded = responses.calls[0].request.body
    assert isinstance(encoded, (str, bytes))
    payload = json.loads(encoded)
    assert payload["body"]["content"] == html_body
    assert review_url in payload["body"]["content"]
    assert unsubscribe_url in payload["body"]["content"]


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

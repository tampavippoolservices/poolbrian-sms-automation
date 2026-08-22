from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlencode

from requests import RequestException
from twilio.base.exceptions import TwilioException, TwilioRestException
from twilio.rest import Client


@dataclass(frozen=True, slots=True)
class SendResult:
    provider_message_id: str
    provider_status: str


class SmsSendError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
        uncertain: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain


class TwilioSmsClient:
    def __init__(self, public_base_url: str) -> None:
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        self.from_number = os.getenv("TWILIO_PHONE_NUMBER", "")
        self.public_base_url = public_base_url.rstrip("/")
        if not self.account_sid or not self.auth_token or not self.from_number:
            raise SmsSendError("Twilio configuration is incomplete")
        self.client = Client(self.account_sid, self.auth_token)

    def send(self, *, destination: str, body: str, message_job_id: int) -> SendResult:
        query = urlencode({"job_id": str(message_job_id)})
        callback = f"{self.public_base_url}/webhooks/twilio/status?{query}"
        try:
            message = self.client.messages.create(
                body=body,
                from_=self.from_number,
                to=destination,
                status_callback=callback,
            )
        except TwilioRestException as exc:
            status = int(exc.status or 0)
            retryable = status in {408, 409, 425, 429} or status >= 500
            raise SmsSendError(
                str(exc),
                code=str(exc.code) if exc.code is not None else None,
                retryable=retryable,
                uncertain=status in {408, 409, 425} or status >= 500,
            ) from exc
        except (RequestException, TwilioException) as exc:
            raise SmsSendError(
                "Twilio send result is unknown after a network/client error",
                code="network_error",
                uncertain=True,
            ) from exc
        return SendResult(
            provider_message_id=message.sid, provider_status=message.status or "accepted"
        )

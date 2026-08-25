from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from requests import RequestException

from app.repositories.oauth import get_refresh_token
from app.services.http import resilient_session

MICROSOFT_SCOPES = "openid profile email offline_access Mail.Send Mail.ReadWrite"


class MicrosoftApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        code: str | None = None,
        uncertain: bool = False,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.code = code
        self.uncertain = uncertain


@dataclass(frozen=True, slots=True)
class EmailSendResult:
    provider_message_id: str
    provider_status: str


class MicrosoftGraphClient:
    def __init__(self) -> None:
        self.tenant_id = os.getenv("MICROSOFT_TENANT_ID", "common")
        self.client_id = os.getenv("MICROSOFT_CLIENT_ID", "")
        self.client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "")
        self.redirect_uri = os.getenv("MICROSOFT_OAUTH_REDIRECT_URI", "")
        self.sender = os.getenv("OUTLOOK_SENDER_ADDRESS", "")
        self.bounce_folder = os.getenv("OUTLOOK_BOUNCE_FOLDER", "inbox").strip() or "inbox"
        self.session = resilient_session()
        self._access_token: str | None = None

    @property
    def token_url(self) -> str:
        return f"https://login.microsoftonline.com/{quote(self.tenant_id)}/oauth2/v2.0/token"

    def authorization_url(
        self,
        state: str,
        *,
        redirect_uri: str | None = None,
        scopes: str = MICROSOFT_SCOPES,
    ) -> str:
        selected_redirect = redirect_uri or self.redirect_uri
        if not self.client_id or not selected_redirect:
            raise MicrosoftApiError("Microsoft OAuth configuration is incomplete")
        endpoint = (
            f"https://login.microsoftonline.com/{quote(self.tenant_id)}/oauth2/v2.0/authorize"
        )
        return (
            endpoint
            + "?"
            + urlencode(
                {
                    "client_id": self.client_id,
                    "response_type": "code",
                    "redirect_uri": selected_redirect,
                    "response_mode": "query",
                    "scope": scopes,
                    "state": state,
                    "prompt": "consent",
                }
            )
        )

    def exchange_code(
        self,
        code: str,
        *,
        redirect_uri: str | None = None,
        scopes: str = MICROSOFT_SCOPES,
    ) -> dict[str, Any]:
        response = self.session.post(
            self.token_url,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri or self.redirect_uri,
                "grant_type": "authorization_code",
                "scope": scopes,
            },
            timeout=(3.05, 15),
        )
        self._raise(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise MicrosoftApiError("Microsoft token response is invalid")
        return payload

    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        refresh_token = get_refresh_token("microsoft")
        if not refresh_token:
            raise MicrosoftApiError("Microsoft Outlook is not connected")
        response = self.session.post(
            self.token_url,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": MICROSOFT_SCOPES,
            },
            timeout=(3.05, 15),
        )
        self._raise(response)
        token = response.json().get("access_token")
        if not token:
            raise MicrosoftApiError("Microsoft did not return an access token")
        self._access_token = str(token)
        return self._access_token

    def current_user(self, access_token: str) -> dict[str, Any]:
        response = self.session.get(
            "https://graph.microsoft.com/v1.0/me",
            params={"$select": "id,displayName,mail,userPrincipalName"},
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=(3.05, 15),
        )
        self._raise(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise MicrosoftApiError("Microsoft user response is invalid")
        return payload

    def send_email(
        self,
        *,
        destination: str,
        subject: str,
        text_body: str,
        html_body: str,
        message_job_id: int,
    ) -> EmailSendResult:
        endpoint = self._mailbox_endpoint("messages")
        # Create a structured Graph draft instead of uploading raw MIME. Graph's
        # MIME ingestion can preserve quoted-printable soft line breaks inside
        # long HTML attributes, corrupting tracking and unsubscribe URLs.
        draft_payload = {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_body,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": destination,
                    }
                }
            ],
            "internetMessageHeaders": [
                {
                    "name": "X-Tampa-VIP-Job-ID",
                    "value": str(message_job_id),
                }
            ],
        }
        # Keep the text argument in the service contract for provider-neutral
        # workers and future plain-text delivery support.
        _ = text_body
        try:
            response = self.session.post(
                endpoint,
                json=draft_payload,
                headers=self._headers(),
                timeout=(3.05, 20),
            )
        except RequestException as exc:
            # A lost draft response can leave an orphan draft, but no customer message was sent.
            raise MicrosoftApiError(
                "Microsoft draft creation failed before send",
                retryable=True,
                code="network_error",
            ) from exc
        self._raise(response)
        draft = response.json()
        draft_id = draft.get("id")
        if not draft_id:
            raise MicrosoftApiError("Microsoft did not return a draft message ID")
        try:
            send_response = self.session.post(
                self._mailbox_endpoint(f"messages/{quote(str(draft_id), safe='')}/send"),
                headers={**self._headers(), "Content-Length": "0"},
                timeout=(3.05, 20),
            )
        except RequestException as exc:
            raise MicrosoftApiError(
                "Microsoft send result is unknown after a network error",
                code="network_error",
                uncertain=True,
            ) from exc
        self._raise(send_response, mutation_uncertain=True)
        return EmailSendResult(provider_message_id=str(draft_id), provider_status="accepted")

    def recent_inbox_messages(self, limit: int = 50) -> list[dict[str, Any]]:
        params = {
            "$top": str(min(max(limit, 1), 100)),
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,bodyPreview,from,receivedDateTime,isRead,internetMessageHeaders",
        }
        response = self.session.get(
            self._mailbox_endpoint(f"mailFolders/{quote(self.bounce_folder, safe='')}/messages"),
            params=params,
            headers=self._headers(),
            timeout=(3.05, 20),
        )
        self._raise(response)
        values = response.json().get("value", [])
        return values if isinstance(values, list) else []

    def mark_message_read(self, message_id: str) -> None:
        response = self.session.patch(
            self._mailbox_endpoint(f"messages/{quote(message_id, safe='')}"),
            json={"isRead": True},
            headers=self._headers(),
            timeout=(3.05, 15),
        )
        self._raise(response)

    def _mailbox_endpoint(self, suffix: str) -> str:
        mailbox = f"users/{quote(self.sender, safe='')}" if self.sender else "me"
        return f"https://graph.microsoft.com/v1.0/{mailbox}/{suffix}"

    def _headers(self, *, content_type: str = "application/json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token()}",
            "Accept": "application/json",
            "Content-Type": content_type,
        }

    @staticmethod
    def _raise(response, *, mutation_uncertain: bool = False) -> None:
        if response.ok:
            return
        retryable = response.status_code in {408, 409, 425, 429} or response.status_code >= 500
        try:
            payload = response.json()
            error = payload.get("error", {})
            code = str(error.get("code") or response.status_code)
            message = str(error.get("message") or response.text)
        except ValueError:
            code = str(response.status_code)
            message = response.text
        uncertain = mutation_uncertain and (
            response.status_code in {408, 409, 425} or response.status_code >= 500
        )
        raise MicrosoftApiError(
            message[:1000],
            retryable=retryable and not uncertain,
            code=code,
            uncertain=uncertain,
        )

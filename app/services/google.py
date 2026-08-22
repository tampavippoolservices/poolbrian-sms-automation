from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode

from app.repositories.oauth import get_refresh_token
from app.services.http import resilient_session

GOOGLE_SCOPE = "https://www.googleapis.com/auth/business.manage"


class GoogleApiError(RuntimeError):
    pass


class GoogleBusinessClient:
    def __init__(self) -> None:
        self.client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
        self.client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
        self.redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")
        self.session = resilient_session()
        self._access_token: str | None = None

    def authorization_url(self, state: str) -> str:
        if not self.client_id or not self.redirect_uri:
            raise GoogleApiError("Google OAuth configuration is incomplete")
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": GOOGLE_SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
            }
        )

    def exchange_code(self, code: str) -> dict[str, Any]:
        response = self.session.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=(3.05, 15),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise GoogleApiError("Google token response is invalid")
        return payload

    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        refresh_token = get_refresh_token("google")
        if not refresh_token:
            raise GoogleApiError("Google is not connected")
        response = self.session.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=(3.05, 15),
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise GoogleApiError("Google did not return an access token")
        self._access_token = str(token)
        return self._access_token

    def list_accounts(self) -> list[dict[str, Any]]:
        response = self.session.get(
            "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
            headers=self._headers(),
            timeout=(3.05, 15),
        )
        response.raise_for_status()
        accounts = response.json().get("accounts", [])
        return accounts if isinstance(accounts, list) else []

    def iter_reviews(self, account_id: str, location_id: str) -> Iterator[dict[str, Any]]:
        if account_id.startswith("accounts/"):
            account_id = account_id.split("/", 1)[1]
        if location_id.startswith("locations/"):
            location_id = location_id.split("/", 1)[1]
        url = (
            "https://mybusiness.googleapis.com/v4/accounts/"
            f"{account_id}/locations/{location_id}/reviews"
        )
        page_token: str | None = None
        while True:
            params = {"pageSize": "50", "orderBy": "updateTime desc"}
            if page_token:
                params["pageToken"] = page_token
            response = self.session.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=(3.05, 20),
            )
            response.raise_for_status()
            payload = response.json()
            reviews = payload.get("reviews", [])
            if not isinstance(reviews, list):
                raise GoogleApiError("Google reviews response is invalid")
            yield from (review for review in reviews if isinstance(review, dict))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token()}", "Accept": "application/json"}

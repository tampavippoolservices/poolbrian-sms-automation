import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from database import get_db_connection

def get_google_access_token():
    client_id = os.environ.get(
        "GOOGLE_OAUTH_CLIENT_ID"
    )
    client_secret = os.environ.get(
        "GOOGLE_OAUTH_CLIENT_SECRET"
    )

    if not client_id or not client_secret:
        raise Exception(
            "Google OAuth client configuration is missing"
        )

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT value
                FROM automation_state
                WHERE key = 'google_refresh_token'
            """)

            refresh_row = cur.fetchone()

    if not refresh_row:
        raise Exception(
            "Google Business Profile is not connected"
        )

    refresh_token = refresh_row[0]

    token_request_data = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }).encode("utf-8")

    token_request = Request(
        "https://oauth2.googleapis.com/token",
        data=token_request_data,
        headers={
            "Content-Type": (
                "application/x-www-form-urlencoded"
            )
        },
        method="POST"
    )

    with urlopen(token_request, timeout=15) as response:
        token_data = json.loads(
            response.read().decode("utf-8")
        )

    access_token = token_data.get("access_token")

    if not access_token:
        raise Exception(
            "Google did not return an access token"
        )

    return access_token

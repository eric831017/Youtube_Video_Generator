"""Shared Google OAuth credential management."""
from __future__ import annotations

import json
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from config import (
    CLIENT_SECRETS_PATH,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_OAUTH_SCOPES,
    TOKEN_PATH,
    logger,
)

OOB_REDIRECT = "urn:ietf:wg:oauth:2.0:oob"


def _client_config() -> dict:
    if CLIENT_SECRETS_PATH.exists():
        return json.loads(CLIENT_SECRETS_PATH.read_text(encoding="utf-8"))
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError(
            "Google OAuth not configured. Provide GOOGLE_CLIENT_ID / "
            "GOOGLE_CLIENT_SECRET in .env or data/client_secrets.json."
        )
    return {
        "installed": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [OOB_REDIRECT, "http://localhost"],
        }
    }


def _save_credentials(creds: Credentials) -> None:
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")


def load_credentials() -> Optional[Credentials]:
    if not TOKEN_PATH.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(
            str(TOKEN_PATH), GOOGLE_OAUTH_SCOPES
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load token.json: %s", exc)
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(creds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to refresh Google credentials: %s", exc)
            return None
    return creds


def get_credentials() -> Credentials:
    creds = load_credentials()
    if creds is None or not creds.valid:
        raise RuntimeError(
            "Google credentials missing or invalid. Run /auth in Telegram, "
            "visit the URL, then send the code back with /auth <code>."
        )
    return creds


def build_auth_flow() -> Flow:
    flow = Flow.from_client_config(_client_config(), scopes=GOOGLE_OAUTH_SCOPES)
    flow.redirect_uri = OOB_REDIRECT
    return flow


def build_auth_url() -> str:
    flow = build_auth_flow()
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return url


def complete_auth(code: str) -> Credentials:
    flow = build_auth_flow()
    flow.fetch_token(code=code.strip())
    creds = flow.credentials
    _save_credentials(creds)
    return creds

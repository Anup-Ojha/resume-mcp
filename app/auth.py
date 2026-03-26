"""
Google OAuth 2.0 + Gmail integration.

The OAuth flow is stateless: the Telegram user ID is base64-encoded in the
`state` parameter so the callback endpoint can look up who authenticated.

All Gmail calls use the google-api-python-client (synchronous) and should be
run via FastAPI's run_in_threadpool before awaiting.
"""

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GOOGLE_OAUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL  = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

GMAIL_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _redirect_uri() -> str:
    return f"{settings.public_api_url.rstrip('/')}/auth/google/callback"


def build_auth_url(telegram_user_id: str, source: str = "bot") -> str:
    """
    Return the Google OAuth2 URL the user should visit to connect Gmail.

    source: 'bot'    — traditional flow (link sent in bot message)
            'webapp' — Mini App flow (callback returns a page that calls
                       Telegram.WebApp.sendData and closes)
    """
    from urllib.parse import urlencode
    state_bytes = json.dumps({"tid": str(telegram_user_id), "src": source}).encode()
    state = base64.urlsafe_b64encode(state_bytes).decode()
    params = {
        "client_id":     settings.google_client_id,
        "redirect_uri":  _redirect_uri(),
        "response_type": "code",
        "scope":         " ".join(GMAIL_SCOPES),
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         state,
    }
    return f"{GOOGLE_OAUTH_URL}?{urlencode(params)}"


def _pad_base64(s: str) -> str:
    """Add missing base64 padding so urlsafe_b64decode doesn't raise."""
    return s + "=" * (-len(s) % 4)


def decode_state(state: str) -> Optional[str]:
    """Decode base64 state → telegram_user_id string, or None on error."""
    try:
        data = json.loads(base64.urlsafe_b64decode(_pad_base64(state).encode()).decode())
        return str(data["tid"])
    except Exception:
        return None


def decode_state_full(state: str) -> Optional[dict]:
    """Decode base64 state → full dict {tid, src}, or None on error."""
    try:
        return json.loads(base64.urlsafe_b64decode(_pad_base64(state).encode()).decode())
    except Exception:
        return None


# ── Token exchange ────────────────────────────────────────────────────────────

async def exchange_code(
    code: str,
    state: str,
) -> Tuple[bool, Optional[Dict], Optional[Dict], str]:
    """
    Exchange authorization code for tokens and fetch Google user info.

    Returns:
        (success, user_info, tokens, message)
        user_info keys: sub, email, name, picture
        tokens keys: access_token, refresh_token, token_expiry (ISO str), scopes
    """
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code":          code,
                "client_id":     settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri":  _redirect_uri(),
                "grant_type":    "authorization_code",
            },
            timeout=15,
        )

    if token_resp.status_code != 200:
        return False, None, None, f"Token exchange failed: {token_resp.text}"

    token_data = token_resp.json()
    access_token  = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in    = token_data.get("expires_in", 3600)

    if not access_token:
        return False, None, None, "No access_token in response"

    expiry_ts  = datetime.now(timezone.utc).timestamp() + expires_in
    expiry_iso = datetime.fromtimestamp(expiry_ts, tz=timezone.utc).isoformat()

    # Fetch Google user info
    async with httpx.AsyncClient() as client:
        ui_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )

    if ui_resp.status_code != 200:
        return False, None, None, "Failed to fetch user info from Google"

    user_info = ui_resp.json()
    tokens = {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_expiry":  expiry_iso,
        "scopes":        " ".join(GMAIL_SCOPES),
    }
    return True, user_info, tokens, "OK"


async def refresh_access_token(
    refresh_token: str,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Refresh access token. Returns (success, new_access_token, new_expiry_iso)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id":     settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "grant_type":    "refresh_token",
            },
            timeout=15,
        )

    if resp.status_code != 200:
        return False, None, None

    data       = resp.json()
    new_token  = data.get("access_token")
    expires_in = data.get("expires_in", 3600)
    expiry_ts  = datetime.now(timezone.utc).timestamp() + expires_in
    new_expiry = datetime.fromtimestamp(expiry_ts, tz=timezone.utc).isoformat()
    return bool(new_token), new_token, new_expiry


async def revoke_token(token: str) -> bool:
    """Revoke an access or refresh token with Google."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                GOOGLE_REVOKE_URL,
                params={"token": token},
                timeout=10,
            )
        return resp.status_code == 200
    except Exception:
        return False


def _is_expired(token_expiry_iso: str) -> bool:
    try:
        expiry = datetime.fromisoformat(token_expiry_iso)
        return datetime.now(timezone.utc) >= expiry
    except Exception:
        return True


async def get_valid_access_token(
    db,
    telegram_user_id: str,
) -> Tuple[bool, Optional[str], str]:
    """
    Return a valid access token for the user, refreshing if expired.
    Returns (success, access_token, message).
    """
    row = db.get_google_tokens(telegram_user_id)
    if not row:
        return False, None, "Not logged in. Use /login to connect your Google account."

    access_token  = row.get("access_token")
    token_expiry  = row.get("token_expiry")
    refresh_token = row.get("refresh_token")

    if token_expiry and _is_expired(token_expiry):
        if not refresh_token:
            return False, None, "Session expired. Please /login again."
        ok, new_token, new_expiry = await refresh_access_token(refresh_token)
        if not ok or not new_token:
            return False, None, "Failed to refresh session. Please /login again."
        db.update_access_token(telegram_user_id, new_token, new_expiry)
        access_token = new_token

    return True, access_token, "OK"


# ── Gmail (synchronous — wrap with run_in_threadpool in FastAPI) ──────────────

def _gmail_service(access_token: str):
    """Build a gmail API service from a plain access token."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(token=access_token)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _parse_message(detail: dict) -> dict:
    headers = {
        h["name"]: h["value"]
        for h in detail.get("payload", {}).get("headers", [])
    }
    return {
        "id":      detail.get("id"),
        "subject": headers.get("Subject", "(no subject)"),
        "from":    headers.get("From", ""),
        "date":    headers.get("Date", ""),
        "snippet": detail.get("snippet", ""),
    }


def fetch_inbox_sync(
    access_token: str,
    max_results: int = 5,
) -> Tuple[bool, List[dict], str]:
    """Fetch unread inbox messages (synchronous)."""
    try:
        svc  = _gmail_service(access_token)
        resp = svc.users().messages().list(
            userId="me",
            labelIds=["INBOX", "UNREAD"],
            maxResults=max_results,
        ).execute()

        items = resp.get("messages", [])
        if not items:
            return True, [], "No unread messages"

        results = []
        for item in items:
            detail = svc.users().messages().get(
                userId="me",
                id=item["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()
            results.append(_parse_message(detail))

        return True, results, "OK"
    except Exception as e:
        logger.error(f"Gmail inbox error: {e}")
        return False, [], str(e)


def send_email_with_attachment_sync(
    access_token: str,
    to: str,
    subject: str,
    body_text: str,
    attachment_bytes: bytes,
    attachment_filename: str,
) -> Tuple[bool, str]:
    """
    Send an email with a PDF attachment via Gmail API (synchronous).
    Returns (success, message_id_or_error).
    """
    import email.mime.multipart
    import email.mime.text
    import email.mime.base
    import email.encoders
    import base64 as b64

    try:
        msg = email.mime.multipart.MIMEMultipart()
        msg["to"]      = to
        msg["subject"] = subject
        msg.attach(email.mime.text.MIMEText(body_text, "plain"))

        part = email.mime.base.MIMEBase("application", "pdf")
        part.set_payload(attachment_bytes)
        email.encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{attachment_filename}"',
        )
        msg.attach(part)

        raw = b64.urlsafe_b64encode(msg.as_bytes()).decode()

        svc    = _gmail_service(access_token)
        result = svc.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        return True, result.get("id", "sent")
    except Exception as e:
        logger.error(f"Gmail send error: {e}")
        return False, str(e)


def search_gmail_sync(
    access_token: str,
    query: str,
    max_results: int = 5,
) -> Tuple[bool, List[dict], str]:
    """Search Gmail (synchronous)."""
    try:
        svc  = _gmail_service(access_token)
        resp = svc.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results,
        ).execute()

        items = resp.get("messages", [])
        if not items:
            return True, [], "No messages found"

        results = []
        for item in items:
            detail = svc.users().messages().get(
                userId="me",
                id=item["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()
            results.append(_parse_message(detail))

        return True, results, "OK"
    except Exception as e:
        logger.error(f"Gmail search error: {e}")
        return False, [], str(e)

"""
Telegram Mini App initData verification (Phase 2).

Telegram cryptographically signs the initData string passed to Mini Apps.
We verify this signature using HMAC-SHA256 before trusting any user data
from the Mini App.

Reference: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Usage:
    from app.telegram_auth import verify_init_data, parse_init_data_user

    try:
        user = verify_init_data(init_data_string)
        # user = {"id": 123456, "first_name": "...", "username": "..."}
    except ValueError as e:
        # Signature invalid — reject the request
        raise HTTPException(403, str(e))
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, unquote

logger = logging.getLogger(__name__)

# Maximum age of initData before we consider it stale (seconds)
# 10 minutes is standard; Telegram itself does not expire it but we do for security
INIT_DATA_MAX_AGE_SECONDS = 600


def verify_init_data(
    init_data: str,
    bot_token: str,
    check_age: bool = True,
) -> Dict[str, Any]:
    """
    Verify Telegram Mini App initData signature and return the parsed user dict.

    Args:
        init_data:  Raw initData string from Telegram.WebApp.initData
        bot_token:  Your Telegram bot token (used as HMAC key)
        check_age:  If True, reject data older than INIT_DATA_MAX_AGE_SECONDS

    Returns:
        Parsed user dict: {"id": int, "first_name": str, "username": str, ...}

    Raises:
        ValueError: if signature is invalid, expired, or data is malformed
    """
    if not init_data:
        raise ValueError("Empty initData")

    # Parse the query-string-encoded initData into key=value pairs
    params = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = params.pop("hash", None)
    if not received_hash:
        raise ValueError("initData missing 'hash' field")

    # Build data_check_string: sorted key=value lines, one per line, no hash
    data_check_lines = sorted(f"{k}={v}" for k, v in params.items())
    data_check_string = "\n".join(data_check_lines)

    # Compute HMAC-SHA256 key = HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    # Compute expected hash
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("initData signature verification failed")

    # Optionally check age
    if check_age:
        auth_date = params.get("auth_date")
        if auth_date:
            try:
                age = int(time.time()) - int(auth_date)
                if age > INIT_DATA_MAX_AGE_SECONDS:
                    raise ValueError(f"initData is too old ({age}s > {INIT_DATA_MAX_AGE_SECONDS}s)")
            except ValueError as e:
                if "too old" in str(e):
                    raise
                # auth_date not parseable — skip age check

    # Parse user JSON
    user_raw = params.get("user")
    if not user_raw:
        raise ValueError("initData missing 'user' field")

    try:
        user = json.loads(unquote(user_raw))
    except (json.JSONDecodeError, Exception) as e:
        raise ValueError(f"Could not parse user JSON: {e}")

    return user


def parse_init_data_user(init_data: str) -> Optional[Dict[str, Any]]:
    """
    Parse user from initData WITHOUT verifying signature.
    Only use this for non-security-critical display purposes (e.g. pre-filling name).
    Never trust this for auth decisions.
    """
    try:
        params = dict(parse_qsl(init_data, keep_blank_values=True))
        user_raw = params.get("user", "")
        if user_raw:
            return json.loads(unquote(user_raw))
    except Exception:
        pass
    return None

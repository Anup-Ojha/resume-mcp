"""
app/auth — Authentication layer

Submodules:
  google   — Google OAuth 2.0 + Gmail token management
  telegram — Telegram Mini App initData verification

Common usage:
  from app.auth import google as google_auth
  from app.auth.telegram import verify_init_data
"""

from app.auth import google
from app.auth import telegram
from app.auth.google import (
    build_auth_url,
    exchange_code,
    decode_state,
    decode_state_full,
    refresh_access_token,
    revoke_token,
    get_valid_access_token,
)
from app.auth.telegram import verify_init_data, parse_init_data_user

__all__ = [
    "google", "telegram",
    "build_auth_url", "exchange_code", "decode_state", "decode_state_full",
    "refresh_access_token", "revoke_token", "get_valid_access_token",
    "verify_init_data", "parse_init_data_user",
]

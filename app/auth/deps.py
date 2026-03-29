"""
app/auth/deps.py — Shared FastAPI security dependencies

Usage in route handlers:
    from app.auth.deps import require_user, require_same_user

    @router.get("/api/something")
    async def endpoint(uid: str = Depends(require_user)):
        ...

    @router.get("/api/users/{telegram_id}/profile")
    async def profile(telegram_id: str, _=Depends(require_same_user)):
        ...
"""

import logging
import re
from typing import Optional

from fastapi import Depends, Header, HTTPException, Path, Request

from app.db.crud import db

logger = logging.getLogger(__name__)

_SAFE_UID = re.compile(r"^\d{5,20}$")   # telegram IDs are numeric, 5-20 digits


def _extract_uid(request: Request, header_uid: Optional[str]) -> str:
    """
    Pull user ID from (in priority order):
      1. X-User-Id header  (web app & any HTTP client)
      2. user_id query param
    Returns the raw string (not yet validated).
    """
    uid = (header_uid or "").strip()
    if not uid:
        uid = request.query_params.get("user_id", "").strip()
    return uid


async def require_user(
    request: Request,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
) -> str:
    """
    Dependency: validates that a real, registered user is making the request.

    - Reads uid from X-User-Id header (preferred) or ?user_id= query param.
    - Verifies the uid is a valid integer that exists in the DB.
    - Returns the cleaned uid string on success.
    - Raises HTTP 401 on failure.
    """
    uid = _extract_uid(request, x_user_id)

    if not uid:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Pass your user ID via X-User-Id header.",
        )

    if not _SAFE_UID.match(uid):
        raise HTTPException(status_code=401, detail="Invalid user identifier.")

    user = await db.async_get_telegram_user(int(uid))
    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found. Please sign in first.",
        )

    return uid


async def require_same_user(
    telegram_id: str = Path(...),
    uid: str = Depends(require_user),
) -> str:
    """
    Dependency: same as require_user, PLUS checks that the requesting user
    matches the {telegram_id} path parameter.
    Prevents user A from reading user B's balance/profile.
    """
    if uid != telegram_id.strip():
        raise HTTPException(
            status_code=403,
            detail="Access denied: you can only access your own data.",
        )
    return uid

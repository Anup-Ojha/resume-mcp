"""
app/routers/users.py — User sessions, token balance, and profile management

Routes:
  POST /api/users/session                    → Create or retrieve user session
  GET  /api/users/{telegram_id}/balance      → Get token balance
  POST /api/users/{telegram_id}/deduct       → Deduct tokens (internal use)
  GET  /api/users/{telegram_id}/profile      → Get full user profile
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from datetime import datetime, timezone

from app.db.crud import db
from app.auth.deps import require_same_user

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/users/session")
async def create_or_get_user_session(request: Request):
    """Create or get a user session. Called by bot on /start."""
    data = await request.json()
    telegram_id = str(data.get("telegram_id", ""))
    first_name = data.get("first_name", "")
    username = data.get("username", "")

    if not telegram_id:
        raise HTTPException(status_code=400, detail="telegram_id required")

    try:
        profile = await db.async_get_or_create_telegram_user(
            int(telegram_id), first_name, username
        )
        if profile and not profile.get("is_registered"):
            await db.async_mark_registered(int(telegram_id))
            profile = await db.async_get_telegram_user(int(telegram_id))
        return {"ok": True, "user": profile}
    except Exception as e:
        logger.error(f"Session error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/users/{telegram_id}/balance")
async def get_token_balance(telegram_id: str, _uid: str = Depends(require_same_user)):
    """Get token balance for a user (auto-creates if not found)."""
    try:
        profile = await db.async_get_telegram_user(int(telegram_id))
        if not profile:
            # Auto-create user so balance always works
            profile = await db.async_get_or_create_telegram_user(int(telegram_id))
        if not profile:
            raise HTTPException(status_code=404, detail="User not found")

        tokens = profile.get("tokens_remaining", 0)
        plan = profile.get("plan", "free")
        reset_at = profile.get("tokens_reset_at")

        # Calculate days until reset (no third-party imports needed)
        days_until_reset = None
        if reset_at:
            now = datetime.now(timezone.utc)
            if isinstance(reset_at, str):
                reset_dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
            else:
                reset_dt = reset_at
            if reset_dt.tzinfo is None:
                reset_dt = reset_dt.replace(tzinfo=timezone.utc)
            days_until_reset = max(0, (reset_dt - now).days)

        return {
            "ok": True,
            "telegram_id": telegram_id,
            "tokens_remaining": tokens,
            "plan": plan,
            "reset_at": str(reset_at) if reset_at else None,
            "days_until_reset": days_until_reset,
            "token_costs": {"create": 2, "tailor": 1, "update": 1, "apply": 3}
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Balance error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/users/{telegram_id}/deduct")
async def deduct_tokens(telegram_id: str, request: Request, _uid: str = Depends(require_same_user)):
    """Check and deduct tokens for an operation."""
    data = await request.json()
    operation = data.get("operation", "")

    if not operation:
        raise HTTPException(status_code=400, detail="operation required")

    try:
        ok, message = await db.async_check_and_deduct(int(telegram_id), operation)
        return {"ok": ok, "message": message}
    except Exception as e:
        logger.error(f"Deduct error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/users/{telegram_id}/profile")
async def get_user_profile(telegram_id: str, _uid: str = Depends(require_same_user)):
    """Get full user profile."""
    try:
        profile = await db.async_get_telegram_user(int(telegram_id))
        if not profile:
            raise HTTPException(status_code=404, detail="User not found")
        return {"ok": True, "user": profile}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Profile error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

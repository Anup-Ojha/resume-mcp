"""
Database access layer — PostgreSQL via SQLAlchemy async.

Public interface is identical to the old SupabaseDB class so nothing else
in the codebase needs to change (app/auth.py, app/main.py, telegram_bot.py
all call the same method names with the same signatures).

New methods added for Phase 1–3:
  mark_registered, is_user_registered, get_user_uuid, get_full_user_profile
  get_token_balance, check_and_deduct, reset_tokens, log_token_usage
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select, update, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.models import GoogleToken, LegacyUser, ResumeSession, TelegramUser, TokenUsage

logger = logging.getLogger(__name__)

# Token costs per operation — single source of truth
TOKEN_COSTS: Dict[str, int] = {
    "create": 2,
    "tailor": 1,
    "update": 1,
    "apply":  3,
}

FREE_TIER_TOKENS = 5


def _run(coro):
    """
    Run an async coroutine from synchronous context.
    Used so the public interface stays sync-compatible (callers don't need to
    await these methods), while internals are async SQLAlchemy.

    Python 3.12 note: ThreadPoolExecutor threads inherit the parent's asyncio
    context vars (including the running loop), so asyncio.run() would raise
    "cannot be called from a running event loop" even in a new thread.
    We work around this by explicitly creating and managing a new event loop.
    """
    import concurrent.futures

    def _run_in_new_loop():
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()
            asyncio.set_event_loop(None)

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Called from an async context (FastAPI/uvicorn) — run in a fresh thread
        # with its own event loop to avoid context-var loop inheritance.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run_in_new_loop).result()
    else:
        return _run_in_new_loop()


class PostgresDB:
    """
    Drop-in replacement for SupabaseDB.
    All public methods are synchronous and return plain dicts or None,
    exactly as before — so no caller code needs to change.
    """

    # ── Legacy methods (kept for backward compat) ──────────────────────────────

    def get_or_create_user(
        self, phone: str, name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return _run(self._get_or_create_user(phone, name))

    async def _get_or_create_user(
        self, phone: str, name: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(LegacyUser).where(LegacyUser.phone == phone)
            )
            user = result.scalar_one_or_none()
            if user:
                return {"id": str(user.id), "phone": user.phone, "name": user.name}
            new_user = LegacyUser(phone=phone, name=name)
            session.add(new_user)
            await session.commit()
            await session.refresh(new_user)
            return {"id": str(new_user.id), "phone": new_user.phone, "name": new_user.name}

    def create_session(
        self, user_id: str, session_type: str, raw_input: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        return _run(self._create_session(user_id, session_type, raw_input))

    async def _create_session(
        self, user_id: str, session_type: str, raw_input: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        import json
        async with AsyncSessionLocal() as session:
            new_session = ResumeSession(
                user_id=uuid.UUID(user_id),
                session_type=session_type,
                raw_input=json.dumps(raw_input),
                status="pending",
            )
            session.add(new_session)
            await session.commit()
            await session.refresh(new_session)
            return {
                "id": str(new_session.id),
                "user_id": str(new_session.user_id),
                "session_type": new_session.session_type,
                "status": new_session.status,
            }

    def update_session(
        self, session_id: str, pdf_filename: str, status: str = "done"
    ) -> bool:
        return _run(self._update_session(session_id, pdf_filename, status))

    async def _update_session(
        self, session_id: str, pdf_filename: str, status: str
    ) -> bool:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                update(ResumeSession)
                .where(ResumeSession.id == uuid.UUID(session_id))
                .values(pdf_filename=pdf_filename, status=status, updated_at=datetime.now(timezone.utc))
            )
            await session.commit()
            return result.rowcount > 0

    # ── Telegram users ─────────────────────────────────────────────────────────

    def get_or_create_telegram_user(
        self,
        telegram_id: int,
        first_name: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return _run(self._get_or_create_telegram_user(telegram_id, first_name, username))

    async def _get_or_create_telegram_user(
        self,
        telegram_id: int,
        first_name: Optional[str],
        username: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if user:
                return self._user_to_dict(user)
            new_user = TelegramUser(
                telegram_id=telegram_id,
                first_name=first_name,
                username=username,
            )
            session.add(new_user)
            await session.commit()
            await session.refresh(new_user)
            return self._user_to_dict(new_user)

    def get_telegram_user(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        return _run(self._get_telegram_user(int(telegram_id)))

    async def _get_telegram_user(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            return self._user_to_dict(user) if user else None

    # ── Google tokens ──────────────────────────────────────────────────────────

    def save_google_tokens(
        self,
        telegram_id: str,
        access_token: str,
        refresh_token: Optional[str],
        token_expiry: str,
        scopes: str,
        google_id: str,
        email: str,
        full_name: str,
        avatar_url: Optional[str] = None,
    ) -> bool:
        return _run(self._save_google_tokens(
            int(telegram_id), access_token, refresh_token,
            token_expiry, scopes, google_id, email, full_name, avatar_url
        ))

    async def _save_google_tokens(
        self,
        telegram_id: int,
        access_token: str,
        refresh_token: Optional[str],
        token_expiry: str,
        scopes: str,
        google_id: str,
        email: str,
        full_name: str,
        avatar_url: Optional[str],
    ) -> bool:
        try:
            expiry_dt = datetime.fromisoformat(token_expiry)
        except Exception:
            expiry_dt = None

        try:
            async with AsyncSessionLocal() as session:
                # Update telegram_users with Google profile
                await session.execute(
                    update(TelegramUser)
                    .where(TelegramUser.telegram_id == telegram_id)
                    .values(
                        google_id=google_id,
                        google_email=email,
                        google_name=full_name,
                        google_avatar=avatar_url,
                        last_seen_at=datetime.now(timezone.utc),
                    )
                )

                # Upsert google_tokens
                stmt = pg_insert(GoogleToken).values(
                    telegram_user_id=telegram_id,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    token_expiry=expiry_dt,
                    scopes=scopes,
                    updated_at=datetime.now(timezone.utc),
                ).on_conflict_do_update(
                    index_elements=["telegram_user_id"],
                    set_={
                        "access_token":  access_token,
                        "token_expiry":  expiry_dt,
                        "scopes":        scopes,
                        "updated_at":    datetime.now(timezone.utc),
                        **({"refresh_token": refresh_token} if refresh_token else {}),
                    }
                )
                await session.execute(stmt)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"save_google_tokens error: {e}")
            return False

    def get_google_tokens(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        return _run(self._get_google_tokens(int(telegram_id)))

    async def _get_google_tokens(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(GoogleToken).where(GoogleToken.telegram_user_id == telegram_id)
            )
            tok = result.scalar_one_or_none()
            if not tok:
                return None
            return {
                "telegram_user_id": str(tok.telegram_user_id),
                "access_token":     tok.access_token,
                "refresh_token":    tok.refresh_token,
                "token_expiry":     tok.token_expiry.isoformat() if tok.token_expiry else None,
                "scopes":           tok.scopes,
            }

    async def async_get_google_tokens(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        return await self._get_google_tokens(int(telegram_id))

    async def async_update_access_token(self, telegram_id: str, new_token: str, new_expiry: str) -> bool:
        return await self._update_access_token(int(telegram_id), new_token, new_expiry)

    def delete_google_tokens(self, telegram_id: str) -> bool:
        return _run(self._delete_google_tokens(int(telegram_id)))

    async def async_delete_google_tokens(self, telegram_id: int) -> bool:
        """Awaitable version — call this from FastAPI endpoints."""
        return await self._delete_google_tokens(telegram_id)

    async def _delete_google_tokens(self, telegram_id: int) -> bool:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(GoogleToken).where(GoogleToken.telegram_user_id == telegram_id)
            )
            await session.commit()
            return result.rowcount > 0

    def update_access_token(
        self, telegram_id: str, new_token: str, new_expiry: str
    ) -> bool:
        return _run(self._update_access_token(int(telegram_id), new_token, new_expiry))

    async def _update_access_token(
        self, telegram_id: int, new_token: str, new_expiry: str
    ) -> bool:
        try:
            expiry_dt = datetime.fromisoformat(new_expiry)
        except Exception:
            expiry_dt = None
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                update(GoogleToken)
                .where(GoogleToken.telegram_user_id == telegram_id)
                .values(
                    access_token=new_token,
                    token_expiry=expiry_dt,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            return result.rowcount > 0

    # ── Phase 1: Registration & Identity ──────────────────────────────────────

    def mark_registered(self, telegram_id: str, user_uuid: Optional[str] = None) -> bool:
        """Mark user as registered and optionally set their UUID."""
        return _run(self._mark_registered(int(telegram_id), user_uuid))

    async def _mark_registered(
        self, telegram_id: int, user_uuid: Optional[str]
    ) -> bool:
        values: Dict[str, Any] = {
            "is_registered": True,
            "last_seen_at":  datetime.now(timezone.utc),
        }
        if user_uuid:
            values["user_uuid"] = user_uuid
        else:
            # Generate one if not provided
            values["user_uuid"] = str(uuid.uuid4())
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                update(TelegramUser)
                .where(TelegramUser.telegram_id == telegram_id)
                .values(**values)
            )
            await session.commit()
            return result.rowcount > 0

    def is_user_registered(self, telegram_id: str) -> bool:
        """Return True if user has completed registration."""
        user = self.get_telegram_user(telegram_id)
        return bool(user and user.get("is_registered"))

    def get_user_uuid(self, telegram_id: str) -> Optional[str]:
        """Return the user's UUID string, or None."""
        user = self.get_telegram_user(telegram_id)
        return user.get("user_uuid") if user else None

    def get_full_user_profile(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        """Alias of get_telegram_user — returns all columns as dict."""
        return self.get_telegram_user(telegram_id)

    # ── Phase 3: Token system ──────────────────────────────────────────────────

    def get_token_balance(self, telegram_id: str) -> int:
        """Return current token balance (0 if user not found)."""
        user = self.get_telegram_user(telegram_id)
        return user.get("tokens_remaining", 0) if user else 0

    def check_and_deduct(
        self, telegram_id: str, operation: str
    ) -> Tuple[bool, str]:
        """
        Check if user has enough tokens for operation.
        If yes: deduct tokens, log usage, return (True, "OK").
        If no:  return (False, human-readable message with reset date).
        Also auto-resets tokens if the 30-day window has passed.
        """
        return _run(self._check_and_deduct(int(telegram_id), operation))

    async def _check_and_deduct(
        self, telegram_id: int, operation: str
    ) -> Tuple[bool, str]:
        cost = TOKEN_COSTS.get(operation, 1)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return False, "User not found. Please /start to register."

            now = datetime.now(timezone.utc)

            # Auto-reset if window expired
            reset_at = user.tokens_reset_at
            if reset_at and reset_at.tzinfo is None:
                reset_at = reset_at.replace(tzinfo=timezone.utc)
            if reset_at and now > reset_at:
                user.tokens_remaining = FREE_TIER_TOKENS
                user.tokens_reset_at  = now + timedelta(days=30)
                await session.commit()
                await session.refresh(user)

            if user.tokens_remaining < cost:
                # Calculate days until reset
                days_left = 0
                if user.tokens_reset_at:
                    delta = user.tokens_reset_at - now
                    days_left = max(0, delta.days)
                return (
                    False,
                    f"❌ Not enough tokens.\n"
                    f"You have *{user.tokens_remaining}* token(s) but this action costs *{cost}*.\n"
                    f"Your tokens reset in *{days_left}* day(s)."
                )

            # Deduct
            user.tokens_remaining -= cost
            await session.commit()

            # Log usage (non-blocking — fire and forget)
            try:
                log_entry = TokenUsage(
                    telegram_id=telegram_id,
                    operation=operation,
                    tokens_used=cost,
                )
                session.add(log_entry)
                await session.commit()
            except Exception as e:
                logger.warning(f"Token usage log failed (non-critical): {e}")

            return True, "OK"

    def reset_tokens(
        self,
        telegram_id: str,
        amount: int = FREE_TIER_TOKENS,
        new_reset_date: Optional[datetime] = None,
    ) -> bool:
        """Manually reset token balance (admin use / testing)."""
        return _run(self._reset_tokens(int(telegram_id), amount, new_reset_date))

    async def _reset_tokens(
        self,
        telegram_id: int,
        amount: int,
        new_reset_date: Optional[datetime],
    ) -> bool:
        reset_date = new_reset_date or (datetime.now(timezone.utc) + timedelta(days=30))
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                update(TelegramUser)
                .where(TelegramUser.telegram_id == telegram_id)
                .values(tokens_remaining=amount, tokens_reset_at=reset_date)
            )
            await session.commit()
            return result.rowcount > 0

    def log_token_usage(
        self, telegram_id: str, operation: str, tokens_used: int
    ) -> bool:
        """Manually log a token usage entry (for external callers)."""
        return _run(self._log_token_usage(int(telegram_id), operation, tokens_used))

    async def _log_token_usage(
        self, telegram_id: int, operation: str, tokens_used: int
    ) -> bool:
        async with AsyncSessionLocal() as session:
            entry = TokenUsage(
                telegram_id=telegram_id,
                operation=operation,
                tokens_used=tokens_used,
            )
            session.add(entry)
            await session.commit()
            return True

    # ── Async public interface (for FastAPI endpoints — avoids _run() in event loop) ──

    async def async_get_or_create_telegram_user(
        self,
        telegram_id: int,
        first_name: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Awaitable version — call this from FastAPI endpoints."""
        return await self._get_or_create_telegram_user(telegram_id, first_name, username)

    async def async_mark_registered(
        self, telegram_id: int, user_uuid: Optional[str] = None
    ) -> bool:
        """Awaitable version — call this from FastAPI endpoints."""
        return await self._mark_registered(telegram_id, user_uuid)

    async def async_get_telegram_user(
        self, telegram_id: int
    ) -> Optional[Dict[str, Any]]:
        """Awaitable version — call this from FastAPI endpoints."""
        return await self._get_telegram_user(telegram_id)

    async def async_get_telegram_user_by_google_id(
        self, google_id: str
    ) -> Optional[Dict[str, Any]]:
        """Find an existing user by their Google account ID (prevents token reset on re-login).
        Uses first() to handle duplicate rows from the old bug gracefully."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TelegramUser)
                .where(TelegramUser.google_id == google_id)
                .order_by(TelegramUser.telegram_id)
            )
            user = result.scalars().first()
            return self._user_to_dict(user) if user else None

    async def async_save_google_tokens(
        self,
        telegram_id: int,
        access_token: str,
        refresh_token: Optional[str],
        token_expiry: str,
        scopes: str,
        google_id: str,
        email: str,
        full_name: str,
        avatar_url: Optional[str] = None,
    ) -> bool:
        """Awaitable version — call this from FastAPI endpoints."""
        return await self._save_google_tokens(
            telegram_id, access_token, refresh_token,
            token_expiry, scopes, google_id, email, full_name, avatar_url,
        )

    async def async_check_and_deduct(
        self, telegram_id: int, operation: str
    ) -> Tuple[bool, str]:
        """Awaitable version — call this from FastAPI endpoints."""
        return await self._check_and_deduct(telegram_id, operation)

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _user_to_dict(user: TelegramUser) -> Dict[str, Any]:
        """Convert ORM model to plain dict (same keys as old Supabase rows)."""
        reset_at = user.tokens_reset_at
        if reset_at and reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=timezone.utc)

        return {
            "telegram_id":       str(user.telegram_id),
            "first_name":        user.first_name,
            "username":          user.username,
            "google_id":         user.google_id,
            "google_email":      user.google_email,
            "google_name":       user.google_name,
            "google_avatar":     user.google_avatar,
            "created_at":        user.created_at.isoformat() if user.created_at else None,
            "last_seen_at":      user.last_seen_at.isoformat() if user.last_seen_at else None,
            # Phase 1
            "user_uuid":         user.user_uuid,
            "is_registered":     user.is_registered,
            # Phase 3
            "tokens_remaining":  user.tokens_remaining,
            "tokens_reset_at":   reset_at.isoformat() if reset_at else None,
            # Phase 4 (stored, not used yet)
            "plan":              user.plan,
            "plan_expires_at":   user.plan_expires_at.isoformat() if user.plan_expires_at else None,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
# Drop-in replacement — everything that imported `from app.db import db` keeps working.
db = PostgresDB()

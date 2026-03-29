"""
SQLAlchemy ORM models — replaces Supabase schema.

Tables:
  telegram_users  — one row per Telegram user (identity + auth + tokens)
  google_tokens   — OAuth tokens per user (Gmail access)
  token_usage     — per-operation usage log
  users           — legacy phone-based users (kept for backward compat)
  resume_sessions — legacy resume session tracking
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey,
    Integer, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_plus30() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=30)


class Base(DeclarativeBase):
    pass


# ── Telegram Users ─────────────────────────────────────────────────────────────

class TelegramUser(Base):
    __tablename__ = "telegram_users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Telegram profile
    first_name:    Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    username:      Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Google OAuth profile (populated after sign-in)
    google_id:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    google_email:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    google_name:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    google_avatar: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    last_seen_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )

    # ── Phase 1: Identity & Auth gate ──────────────────────────────────────────
    user_uuid:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_registered: Mapped[bool]          = mapped_column(Boolean, default=False, server_default="false")

    # ── Phase 3: Token system ──────────────────────────────────────────────────
    tokens_remaining: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    tokens_reset_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow_plus30
    )

    # ── Subscription (Phase 4 — stored now, not used yet) ─────────────────────
    # plan: 'free' | 'pro' | 'unlimited'
    plan:            Mapped[str]              = mapped_column(Text, default="free", server_default="'free'")
    plan_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Google OAuth Tokens ────────────────────────────────────────────────────────

class GoogleToken(Base):
    __tablename__ = "google_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("telegram_users.telegram_id"), unique=True, nullable=False
    )
    access_token:  Mapped[str]           = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expiry:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes:        Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


# ── Token Usage Log ────────────────────────────────────────────────────────────

class TokenUsage(Base):
    __tablename__ = "token_usage"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("telegram_users.telegram_id"), nullable=False
    )
    # 'create' | 'tailor' | 'update' | 'apply'
    operation:   Mapped[str] = mapped_column(Text, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


# ── User Resumes (3-slot storage per user) ────────────────────────────────────

class UserResume(Base):
    """
    Tracks the 3 resume slots per user.
    slot: 'master' (full resume from /create) or 'tailored_1'/'tailored_2' (latest tailored versions).
    Filename convention: {telegram_id}_{slot}.pdf  e.g. 1234567890_master.pdf
    """
    __tablename__ = "user_resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("telegram_users.telegram_id"), nullable=False
    )
    # 'master' | 'tailored_1' | 'tailored_2'
    slot: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    job_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # shown in /list

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("telegram_id", "slot", name="uq_user_resume_slot"),
    )


# ── Legacy Tables (kept for backward compatibility) ───────────────────────────

class LegacyUser(Base):
    """Legacy phone-based user — kept but not used in new flows."""
    __tablename__ = "users"

    id:         Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone:      Mapped[str]           = mapped_column(Text, unique=True, nullable=False)
    name:       Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now())


class ResumeSession(Base):
    """Legacy resume session tracking — kept for backward compat."""
    __tablename__ = "resume_sessions"

    id:           Mapped[uuid.UUID]   = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:      Mapped[uuid.UUID]   = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    session_type: Mapped[str]         = mapped_column(Text, nullable=False)
    status:       Mapped[str]         = mapped_column(Text, default="pending", server_default="'pending'")
    pdf_filename: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_input:    Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at:   Mapped[datetime]    = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now())
    updated_at:   Mapped[datetime]    = mapped_column(DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow)

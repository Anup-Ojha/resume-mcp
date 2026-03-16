"""Initial schema — telegram_users, google_tokens, users, resume_sessions

Revision ID: 001
Revises:
Create Date: 2026-03-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on:    Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── telegram_users ─────────────────────────────────────────────────────────
    op.create_table(
        "telegram_users",
        sa.Column("telegram_id",   sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("first_name",    sa.Text(),       nullable=True),
        sa.Column("username",      sa.Text(),       nullable=True),
        sa.Column("google_id",     sa.Text(),       nullable=True),
        sa.Column("google_email",  sa.Text(),       nullable=True),
        sa.Column("google_name",   sa.Text(),       nullable=True),
        sa.Column("google_avatar", sa.Text(),       nullable=True),
        sa.Column("created_at",    sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at",  sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── google_tokens ──────────────────────────────────────────────────────────
    op.create_table(
        "google_tokens",
        sa.Column("id",               UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("telegram_user_id", sa.BigInteger(),    sa.ForeignKey("telegram_users.telegram_id"), unique=True, nullable=False),
        sa.Column("access_token",     sa.Text(),          nullable=False),
        sa.Column("refresh_token",    sa.Text(),          nullable=True),
        sa.Column("token_expiry",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes",           sa.Text(),          nullable=True),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── users (legacy phone-based) ─────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",         UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("phone",      sa.Text(),          unique=True, nullable=False),
        sa.Column("name",       sa.Text(),          nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── resume_sessions (legacy) ───────────────────────────────────────────────
    op.create_table(
        "resume_sessions",
        sa.Column("id",           UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id",      UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_type", sa.Text(),          nullable=False),
        sa.Column("status",       sa.Text(),          server_default="'pending'", nullable=False),
        sa.Column("pdf_filename", sa.Text(),          nullable=True),
        sa.Column("raw_input",    sa.Text(),          nullable=True),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",   sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("resume_sessions")
    op.drop_table("users")
    op.drop_table("google_tokens")
    op.drop_table("telegram_users")

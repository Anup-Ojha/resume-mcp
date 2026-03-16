"""Auth gate columns, token system, subscription fields, token_usage table

Revision ID: 002
Revises: 001
Create Date: 2026-03-16

New columns on telegram_users:
  user_uuid        — stable UUID identity (Phase 1)
  is_registered    — auth gate flag (Phase 1)
  tokens_remaining — rolling token balance (Phase 3)
  tokens_reset_at  — next auto-refill date (Phase 3)
  plan             — subscription tier: free|pro|unlimited (Phase 4, stored only)
  plan_expires_at  — subscription expiry (Phase 4, stored only)

New table:
  token_usage — per-operation usage log (Phase 3)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import text
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on:    Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── New columns on telegram_users ─────────────────────────────────────────

    # Phase 1: identity
    op.add_column("telegram_users", sa.Column(
        "user_uuid", sa.Text(), nullable=True
    ))
    op.add_column("telegram_users", sa.Column(
        "is_registered", sa.Boolean(), server_default="false", nullable=False
    ))

    # Phase 3: token system
    op.add_column("telegram_users", sa.Column(
        "tokens_remaining", sa.Integer(), server_default="5", nullable=False
    ))
    op.add_column("telegram_users", sa.Column(
        "tokens_reset_at",
        sa.DateTime(timezone=True),
        server_default=text("NOW() + INTERVAL '30 days'"),
        nullable=False,
    ))

    # Phase 4: subscription (stored now, not used until Phase 4)
    op.add_column("telegram_users", sa.Column(
        "plan", sa.Text(), server_default="'free'", nullable=False
    ))
    op.add_column("telegram_users", sa.Column(
        "plan_expires_at", sa.DateTime(timezone=True), nullable=True
    ))

    # ── New table: token_usage ─────────────────────────────────────────────────
    op.create_table(
        "token_usage",
        sa.Column("id",          UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
        sa.Column("telegram_id", sa.BigInteger(),    sa.ForeignKey("telegram_users.telegram_id"), nullable=False),
        sa.Column("operation",   sa.Text(),           nullable=False),  # create|tailor|update|apply
        sa.Column("tokens_used", sa.Integer(),        nullable=False),
        sa.Column("created_at",  sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Index for fast per-user usage queries
    op.create_index("ix_token_usage_telegram_id", "token_usage", ["telegram_id"])


def downgrade() -> None:
    op.drop_index("ix_token_usage_telegram_id", table_name="token_usage")
    op.drop_table("token_usage")

    for col in ["plan_expires_at", "plan", "tokens_reset_at", "tokens_remaining", "is_registered", "user_uuid"]:
        op.drop_column("telegram_users", col)

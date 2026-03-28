"""
app/db — Database layer

Exports:
  db              — PostgresDB instance (CRUD operations)
  AsyncSessionLocal — async session factory
  engine          — SQLAlchemy async engine
  Base            — declarative base for all ORM models
"""

from app.db.database import AsyncSessionLocal, engine, get_db, db_session
from app.db.models import Base, TelegramUser, GoogleToken, TokenUsage, LegacyUser, ResumeSession
from app.db.crud import db, PostgresDB

__all__ = [
    "db", "PostgresDB",
    "AsyncSessionLocal", "engine", "get_db", "db_session",
    "Base", "TelegramUser", "GoogleToken", "TokenUsage", "LegacyUser", "ResumeSession",
]

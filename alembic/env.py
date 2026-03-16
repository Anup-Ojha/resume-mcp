"""
Alembic environment script.
Uses synchronous psycopg2 for migrations (Alembic CLI doesn't support async).
DATABASE_URL env var should use postgresql+asyncpg for the app,
but we swap the driver to psycopg2 here for migration runs.
"""

import os
import re
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Import all models so Alembic can detect them
from app.models import Base  # noqa: F401

# Alembic Config object — gives access to values in alembic.ini
config = context.config

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate support
target_metadata = Base.metadata


def get_sync_url() -> str:
    """
    Read DATABASE_URL from environment and convert async driver to sync.
    postgresql+asyncpg://... → postgresql+psycopg2://...
    """
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://resumebot:changeme@localhost:5432/resumebot",
    )
    # Replace asyncpg with psycopg2 for synchronous Alembic runs
    url = re.sub(r"postgresql\+asyncpg", "postgresql+psycopg2", url)
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generate SQL without connecting."""
    url = get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connect to DB and apply."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_sync_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

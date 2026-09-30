"""Alembic environment; the database URL is read only from the environment."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from polycodebench_persistence.models import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _database_url() -> str:
    raw_url = os.environ.get("PCB_MIGRATION_DATABASE_URL")
    if not raw_url:
        raise RuntimeError("PCB_MIGRATION_DATABASE_URL must be set to a migration connection")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("migrations require PostgreSQL")
    if not url.drivername.endswith("+psycopg"):
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        _database_url(),
        poolclass=NullPool,
        hide_parameters=True,
        echo=False,
    )
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql("SET ROLE pcb_migrator")
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    compare_type=True,
                    transaction_per_migration=True,
                )
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

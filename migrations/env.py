"""Alembic setup: uses the same database connection as the app (DATABASE_URL)."""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

target_metadata = db.Base.metadata

with db.engine.connect() as connection:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=db.IS_SQLITE)
    with context.begin_transaction():
        context.run_migrations()

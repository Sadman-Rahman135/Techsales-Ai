"""Alembic environment: migrates as the schema owner (MIGRATION_DATABASE_URL), online only.

`-x db_url=...` overrides the URL; the test suite uses it to migrate the separate test database.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.db.models import Base

config = context.config
if config.config_file_name is not None:
    # Keep loggers that already exist (the app's, when migrations run in-process from tests or tooling).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

url = context.get_x_argument(as_dictionary=True).get("db_url") or get_settings().migration_url
engine = create_engine(url, poolclass=pool.NullPool)

with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()

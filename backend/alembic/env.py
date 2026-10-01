"""Alembic migration environment.

Wired to the application's SQLAlchemy metadata and configuration:

* ``target_metadata`` is ``app.db.session.Base.metadata``. Importing
  ``app.models`` registers every ORM model on that metadata so autogenerate
  and ``create_all`` see all four tables.
* The database URL is read from the application ``Settings`` (``DB_URL``)
  rather than the static ``sqlalchemy.url`` in ``alembic.ini`` so migrations
  target the same database the app uses.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Register every ORM model on Base.metadata and expose the app config.
import app.models  # noqa: F401  (import for side-effect registration)
from app.config import get_settings
from app.db.session import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Override the connection URL with the application's configured DB_URL.
config.set_main_option("sqlalchemy.url", get_settings().db_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object Alembic compares the database against for autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # batch mode keeps ALTER-heavy ops working on SQLite
            render_as_batch=connection.dialect.name == "sqlite",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

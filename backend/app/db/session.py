"""SQLAlchemy engine and session factory.

Supports PostgreSQL (production) and SQLite (local/dev and tests) selected via
``Settings.db_url``. The declarative ``Base`` here is the single metadata target
that the ORM models register against and that Alembic migrates.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


def _make_engine(db_url: str) -> Engine:
    """Create an engine, enabling SQLite-specific connect args when needed."""
    connect_args: dict[str, object] = {}
    if db_url.startswith("sqlite"):
        # Allow usage across threads (needed for the FastAPI test client and
        # the dev server). Safe for the single-user local/test build.
        connect_args["check_same_thread"] = False
    return create_engine(db_url, connect_args=connect_args, future=True)


engine: Engine = _make_engine(get_settings().db_url)

SessionLocal = sessionmaker(
    bind=engine, autocommit=False, autoflush=False, future=True
)


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a database session and closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

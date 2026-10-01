"""Unit tests for the Auth Service login/logout flow."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — register all models on Base.metadata
from app.auth.security import verify_token
from app.db.session import Base
from app.services.auth_service import AuthService, InvalidCredentialsError


@pytest.fixture()
def db() -> Session:
    """An isolated in-memory SQLite session with the schema created."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, future=True
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()


def test_login_succeeds_and_issues_valid_token(db: Session):
    """A correct password returns a token whose subject is the user id."""
    service = AuthService(db)
    user = service.register("learner@example.com", "hunter2")
    db.commit()

    result = service.login("learner@example.com", "hunter2")

    assert result.user_id == user.id
    assert verify_token(result.token) == user.id


def test_login_wrong_password_raises(db: Session):
    """An incorrect password is rejected."""
    service = AuthService(db)
    service.register("learner@example.com", "hunter2")
    db.commit()

    with pytest.raises(InvalidCredentialsError):
        service.login("learner@example.com", "wrong")


def test_login_unknown_email_raises(db: Session):
    """An unknown email is rejected with the same error as a bad password."""
    service = AuthService(db)
    with pytest.raises(InvalidCredentialsError):
        service.login("nobody@example.com", "whatever")


def test_login_oauth_only_account_raises(db: Session):
    """An account with no password hash cannot log in via password."""
    from app.models.user import User

    db.add(User(email="oauth@example.com", password_hash=None))
    db.commit()

    service = AuthService(db)
    with pytest.raises(InvalidCredentialsError):
        service.login("oauth@example.com", "anything")


def test_logout_is_noop(db: Session):
    """Logout returns None without error (stateless sessions)."""
    assert AuthService(db).logout() is None

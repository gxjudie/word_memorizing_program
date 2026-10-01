"""Integration tests for the auth routes (login/logout) via TestClient."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register all models on Base.metadata
from app.api.deps import get_db
from app.auth.security import verify_token
from app.db.session import Base
from app.main import create_app
from app.services.auth_service import AuthService


@pytest.fixture()
def session_factory():
    """A fresh in-memory SQLite bound session factory with schema created."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


@pytest.fixture()
def client(session_factory) -> Iterator[TestClient]:
    """A TestClient whose get_db dependency uses the in-memory DB."""
    app = create_app()

    def _override_get_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def seeded_user(session_factory):
    """Create a Learner account to log in as."""
    db = session_factory()
    try:
        user = AuthService(db).register("learner@example.com", "hunter2")
        db.commit()
        return user.id
    finally:
        db.close()


def test_login_success_returns_verifiable_token(client, seeded_user):
    """Valid credentials return a token whose subject is the user id."""
    resp = client.post(
        "/api/auth/login",
        json={"email": "learner@example.com", "password": "hunter2"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert verify_token(body["access_token"]) == seeded_user


def test_login_bad_credentials_returns_401(client, seeded_user):
    """A wrong password is rejected with 401."""
    resp = client.post(
        "/api/auth/login",
        json={"email": "learner@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401


def test_login_unknown_email_returns_401(client):
    """An unknown email is rejected with 401 (same as a bad password)."""
    resp = client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "whatever"},
    )
    assert resp.status_code == 401


def test_logout_returns_success(client):
    """Logout always succeeds (stateless client-side token discard)."""
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

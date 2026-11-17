"""Unit tests for the application's own signed session and its revocation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.auth.session import (
    DEFAULT_SESSION_TTL,
    InvalidSessionError,
    decode_app_session,
    is_app_session_revoked,
    issue_app_session,
    revoke_app_session,
    verify_app_session,
)
from app.db.session import Base
from app.models.revoked_session import RevokedAppSession

SECRET = "test-session-secret-that-is-long-enough"
NOW = datetime(2030, 1, 2, 3, 4, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_app_session_round_trips_with_sid_and_12h_ttl():
    session = issue_app_session("user-123", secret=SECRET)
    claims = decode_app_session(session.token, secret=SECRET)

    assert verify_app_session(session.token, secret=SECRET) == "user-123"
    assert claims.user_id == "user-123"
    assert claims.session_id == session.session_id and len(claims.session_id) <= 64
    assert DEFAULT_SESSION_TTL == timedelta(hours=12)
    assert abs((claims.expires_at - session.expires_at).total_seconds()) < 1


def test_each_session_gets_a_distinct_sid():
    first = issue_app_session("user-123", secret=SECRET)
    second = issue_app_session("user-123", secret=SECRET)
    assert first.session_id != second.session_id


def test_app_session_rejects_tampering_wrong_secret_and_expiry():
    session = issue_app_session("user-123", secret=SECRET)
    # Flip a character in the middle of the signature segment. The last
    # base64url character of a 32-byte HMAC carries padding bits, so changing
    # it can decode to the same bytes; a middle character always changes them.
    header, payload, signature = session.token.split(".")
    mid = len(signature) // 2
    flipped = "A" if signature[mid] != "A" else "B"
    tampered = ".".join(
        [header, payload, signature[:mid] + flipped + signature[mid + 1 :]]
    )
    assert tampered != session.token
    with pytest.raises(InvalidSessionError):
        verify_app_session(tampered, secret=SECRET)
    with pytest.raises(InvalidSessionError):
        verify_app_session(session.token, secret="another-secret")
    expired = issue_app_session(
        "user-123", secret=SECRET, expires_in=timedelta(seconds=-1)
    )
    with pytest.raises(InvalidSessionError):
        verify_app_session(expired.token, secret=SECRET)


def test_revocation_records_sid_until_exp_and_is_idempotent(db):
    session = issue_app_session("user-123", secret=SECRET, now=NOW)
    assert is_app_session_revoked(db, session.session_id) is False

    revoke_app_session(db, session.session_id, session.expires_at, now=NOW)
    revoke_app_session(db, session.session_id, session.expires_at, now=NOW)

    assert is_app_session_revoked(db, session.session_id) is True
    row = db.execute(select(RevokedAppSession)).scalar_one()
    assert row.expires_at == session.expires_at.replace(tzinfo=None)
    assert db.scalar(select(func.count()).select_from(RevokedAppSession)) == 1


def test_expired_revocations_are_purged(db):
    old = issue_app_session("user-1", secret=SECRET, now=NOW)
    revoke_app_session(db, old.session_id, old.expires_at, now=NOW)
    new = issue_app_session("user-2", secret=SECRET, now=NOW + timedelta(hours=13))
    revoke_app_session(db, new.session_id, new.expires_at, now=NOW + timedelta(hours=13))

    assert is_app_session_revoked(db, old.session_id) is False
    assert is_app_session_revoked(db, new.session_id) is True

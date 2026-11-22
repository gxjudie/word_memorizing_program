"""Unit tests for the SQLAlchemy model structure (Google sign-in + Gemini BYOK)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — register all models on Base.metadata
from app.db.session import Base
from app.models import GeminiCredential, RevokedAppSession, User, WordEntry
from app.models.user import (
    DEFAULT_NEW_WORD_LIMIT,
    DEFAULT_REVIEW_WORD_LIMIT,
    STUDY_LIMIT_MAX,
    STUDY_LIMIT_MIN,
)

GOOGLE_ISSUER = "https://accounts.google.com"


@pytest.fixture()
def db() -> Session:
    """Provide an isolated in-memory database with all model tables."""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_user(*, subject: str = "subject-1", email: str = "learner@example.com") -> User:
    """Build a valid Google-OIDC-backed Learner for model tests."""
    return User(
        email=email,
        name="Learner",
        oidc_issuer=GOOGLE_ISSUER,
        oidc_subject=subject,
    )


def test_user_stores_google_identity_without_password_or_client_id() -> None:
    """The User table stores issuer + subject, and no password or client ID."""
    columns = User.__table__.c

    assert "password_hash" not in columns
    assert "oidc_client_id" not in columns
    assert {"email", "name", "oidc_issuer", "oidc_subject", "created_at"}.issubset(
        columns.keys()
    )
    assert columns.name.nullable is True
    assert columns.email.nullable is False


def test_oidc_subject_unique_constraint_is_issuer_and_subject() -> None:
    """``uq_user_oidc_subject`` covers exactly (oidc_issuer, oidc_subject)."""
    constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in User.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert constraints["uq_user_oidc_subject"] == ("oidc_issuer", "oidc_subject")


def test_same_google_identity_cannot_create_two_accounts(db: Session) -> None:
    """Issuer + subject identify at most one account, regardless of email."""
    db.add(make_user(email="first@example.com"))
    db.commit()

    db.add(make_user(email="second@example.com"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.add(make_user(subject="subject-2", email="first@example.com"))
    db.commit()
    assert len(db.scalars(select(User)).all()) == 2


def test_new_user_reads_default_study_settings(db: Session) -> None:
    """A new user row reads New_Word_Limit 50 and Review_Word_Limit 100 (Req 11.1, 11.2)."""
    columns = User.__table__.c
    assert columns.new_word_limit.nullable is False
    assert columns.review_word_limit.nullable is False
    assert (DEFAULT_NEW_WORD_LIMIT, DEFAULT_REVIEW_WORD_LIMIT) == (50, 100)
    assert (STUDY_LIMIT_MIN, STUDY_LIMIT_MAX) == (0, 1000)

    user = make_user()
    db.add(user)
    db.commit()
    db.expire_all()

    stored = db.get(User, user.id)
    assert stored is not None
    assert (stored.new_word_limit, stored.review_word_limit) == (50, 100)

    # The server default covers raw inserts that bypass the ORM default.
    db.execute(
        text(
            "INSERT INTO users (id, email, oidc_issuer, oidc_subject) "
            "VALUES ('raw-user', 'raw@example.com', :issuer, 'subject-raw')"
        ),
        {"issuer": GOOGLE_ISSUER},
    )
    db.commit()
    raw = db.get(User, "raw-user")
    assert raw is not None
    assert (raw.new_word_limit, raw.review_word_limit) == (50, 100)


def test_oauth_credential_model_is_removed() -> None:
    """No Google or OpenAI token storage remains (Req 9.12)."""
    assert "oauth_credentials" not in Base.metadata.tables
    assert not hasattr(app.models, "OAuthCredential")
    assert not hasattr(User, "oauth_credential")


def test_gemini_credential_round_trips_through_one_to_one_relationship(
    db: Session,
) -> None:
    """A User owns at most one encrypted Gemini key with its last-4 hint."""
    user = make_user()
    user.gemini_credential = GeminiCredential(
        api_key_encrypted="gAAAAA-ciphertext", key_hint="abcd"
    )
    db.add(user)
    db.commit()
    db.expire_all()

    stored = db.get(User, user.id)
    assert stored is not None
    assert stored.gemini_credential is not None
    assert stored.gemini_credential.user_id == stored.id
    assert stored.gemini_credential.api_key_encrypted == "gAAAAA-ciphertext"
    assert stored.gemini_credential.key_hint == "abcd"
    assert stored.gemini_credential.created_at is not None
    assert stored.gemini_credential.updated_at is not None
    assert "ciphertext" not in repr(stored.gemini_credential)


def test_gemini_credential_columns_and_uniqueness(db: Session) -> None:
    """The key columns match the design and user_id is unique."""
    columns = GeminiCredential.__table__.c
    assert columns.api_key_encrypted.nullable is False
    assert columns.key_hint.nullable is False
    assert columns.key_hint.type.length == 4
    assert columns.user_id.unique is True
    (fk,) = columns.user_id.foreign_keys
    assert fk.target_fullname == "users.id"
    assert fk.ondelete == "CASCADE"
    assert User.gemini_credential.property.uselist is False

    user = make_user()
    db.add(user)
    db.commit()
    db.add(GeminiCredential(user_id=user.id, api_key_encrypted="c1", key_hint="1111"))
    db.commit()
    db.add(GeminiCredential(user_id=user.id, api_key_encrypted="c2", key_hint="2222"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_user_removes_gemini_credential(db: Session) -> None:
    """The credential row does not outlive its Learner."""
    user = make_user()
    user.gemini_credential = GeminiCredential(api_key_encrypted="c", key_hint="abcd")
    db.add(user)
    db.commit()

    db.delete(user)
    db.commit()

    assert db.scalars(select(GeminiCredential)).all() == []


def test_revoked_app_session_round_trips(db: Session) -> None:
    """A revoked sid is stored with its expiry; sid is the primary key."""
    table = RevokedAppSession.__table__
    assert [column.name for column in table.primary_key.columns] == ["sid"]
    assert table.c.sid.type.length == 64
    assert table.c.expires_at.nullable is False
    assert table.c.expires_at.index is True

    expiry = datetime(2030, 1, 1, 12, 0, 0) + timedelta(hours=12)
    db.add(RevokedAppSession(sid="sid-1", expires_at=expiry))
    db.commit()
    db.expire_all()

    stored = db.get(RevokedAppSession, "sid-1")
    assert stored is not None
    assert stored.expires_at == expiry

    db.expunge_all()
    db.add(RevokedAppSession(sid="sid-1", expires_at=expiry))
    with pytest.raises(IntegrityError):
        db.commit()


def test_existing_word_relationships_and_unique_constraint_are_preserved() -> None:
    """The auth update leaves the established vocabulary graph unchanged."""
    assert User.words.property.back_populates == "user"
    assert WordEntry.user.property.back_populates == "words"
    assert WordEntry.review_records.property.back_populates == "word"
    assert WordEntry.generated_sentences.property.back_populates == "word"

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in WordEntry.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("user_id", "word_form") in unique_columns

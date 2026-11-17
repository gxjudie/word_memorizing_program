"""Focused tests for request-scoped API dependency wiring (task 8.2).

Gemini is mocked with ``httpx.MockTransport``; no test opens a real network
connection.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 -- register all ORM models
from app.api import deps
from app.auth.secrets import SecretCipher
from app.auth.session import issue_app_session, revoke_app_session
from app.config import Settings
from app.db.session import Base
from app.models.gemini_credential import GeminiCredential
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.gemini_key_service import GeminiKeyService
from app.services.review_service import ReviewService
from app.services.sentence_generation.base import MissingGeminiKeyError
from app.services.sentence_generation.gemini_adapter import GeminiAdapter
from app.services.word_service import WordService

TARGET = SimpleNamespace(word_form="勉強", reading="べんきょう", meaning="study")


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        db_url="sqlite://",
        session_secret="dependency-test-session-secret",
        token_encryption_key=Fernet.generate_key().decode("ascii"),
    )


def _make_user(db: Session, subject: str) -> User:
    learner = User(
        email=f"{subject}@example.com",
        name="Learner",
        oidc_issuer="https://accounts.google.com",
        oidc_subject=subject,
    )
    db.add(learner)
    db.commit()
    db.refresh(learner)
    return learner


@pytest.fixture()
def user(db: Session) -> User:
    return _make_user(db, "google-subject")


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _store_key(db: Session, cipher: SecretCipher, user_id: str, key: str) -> None:
    db.add(
        GeminiCredential(
            user_id=user_id,
            api_key_encrypted=cipher.encrypt(key),
            key_hint=key[-4:],
        )
    )
    db.commit()


def _ok_sentence() -> httpx.Response:
    sentence = {
        "text": "毎日勉強する。",
        "tokens": [
            {"surface": "毎日", "reading": "まいにち", "meaning": "every day",
             "is_target": False, "contains_kanji": True},
            {"surface": "勉強", "reading": "べんきょう", "meaning": "study",
             "is_target": True, "contains_kanji": True},
        ],
    }
    return httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(sentence, ensure_ascii=False)}]},
                 "finishReason": "STOP"}
            ]
        },
    )


# ---- get_current_user ------------------------------------------------------


def test_current_user_is_resolved_from_first_party_app_session(
    db: Session, settings: Settings, user: User
) -> None:
    app_session = issue_app_session(user.id, secret=settings.session_secret)

    resolved = deps.get_current_user(_bearer(app_session.token), db, settings)

    assert resolved.id == user.id


def _assert_unauthorized(exc_info: pytest.ExceptionInfo[HTTPException]) -> None:
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Not authenticated"


@pytest.mark.parametrize("credentials", [None, _bearer(""), _bearer("google-access-token")])
def test_current_user_rejects_missing_or_non_app_session(
    db: Session,
    settings: Settings,
    credentials: HTTPAuthorizationCredentials | None,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        deps.get_current_user(credentials, db, settings)
    _assert_unauthorized(exc_info)


def test_current_user_rejects_expired_session(
    db: Session, settings: Settings, user: User
) -> None:
    expired = issue_app_session(
        user.id,
        secret=settings.session_secret,
        now=datetime.now(timezone.utc) - timedelta(hours=13),
    )

    with pytest.raises(HTTPException) as exc_info:
        deps.get_current_user(_bearer(expired.token), db, settings)
    _assert_unauthorized(exc_info)


def test_current_user_rejects_session_signed_with_another_secret(
    db: Session, settings: Settings, user: User
) -> None:
    forged = issue_app_session(user.id, secret="some-other-secret")

    with pytest.raises(HTTPException) as exc_info:
        deps.get_current_user(_bearer(forged.token), db, settings)
    _assert_unauthorized(exc_info)


def test_current_user_rejects_revoked_session_with_the_same_401(
    db: Session, settings: Settings, user: User
) -> None:
    revoked = issue_app_session(user.id, secret=settings.session_secret)
    still_valid = issue_app_session(user.id, secret=settings.session_secret)
    revoke_app_session(db, revoked.session_id, revoked.expires_at)

    with pytest.raises(HTTPException) as exc_info:
        deps.get_current_user(_bearer(revoked.token), db, settings)
    _assert_unauthorized(exc_info)
    # Revocation is per session ID; another session for the same user still works.
    assert deps.get_current_user(_bearer(still_valid.token), db, settings).id == user.id


def test_current_user_rejects_session_for_unknown_user(
    db: Session, settings: Settings
) -> None:
    orphan = issue_app_session("no-such-user", secret=settings.session_secret)

    with pytest.raises(HTTPException) as exc_info:
        deps.get_current_user(_bearer(orphan.token), db, settings)
    _assert_unauthorized(exc_info)


# ---- service wiring ----------------------------------------------------------


def test_domain_and_auth_services_share_the_request_database(
    db: Session, settings: Settings
) -> None:
    auth_service = deps.get_auth_service(db, settings)
    word_service = deps.get_word_service(db)
    review_service = deps.get_review_service(db)

    assert isinstance(auth_service, AuthService)
    assert isinstance(word_service, WordService)
    assert isinstance(review_service, ReviewService)
    assert auth_service._db is db
    assert word_service._db is db
    assert review_service._db is db


def test_review_service_gets_key_presence_check(
    db: Session, settings: Settings, user: User
) -> None:
    review_service = deps.get_review_service(db)
    check = review_service._has_gemini_key

    assert check is not None
    assert check(user.id) is False
    _store_key(db, SecretCipher(settings.token_encryption_key), user.id, "AIza" + "a" * 35)
    assert check(user.id) is True


def test_gemini_key_service_uses_request_db_and_configured_cipher(
    db: Session, settings: Settings, user: User
) -> None:
    cipher = deps.get_secret_cipher(settings)
    key_service = deps.get_gemini_key_service(db, cipher, deps.get_gemini_http_client())
    key = "AIza" + "b" * 35
    _store_key(db, cipher, user.id, key)

    assert isinstance(key_service, GeminiKeyService)
    assert key_service.get_api_key(user.id) == key
    assert key_service.status(user.id).key_hint == key[-4:]


@pytest.mark.parametrize("bad_key", ["", "not-a-fernet-key"])
def test_missing_or_invalid_encryption_key_is_a_key_free_503(bad_key: str) -> None:
    settings = Settings(db_url="sqlite://", token_encryption_key=bad_key)

    with pytest.raises(HTTPException) as exc_info:
        deps.get_secret_cipher(settings)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == deps.KEY_STORAGE_UNAVAILABLE


def test_settings_expose_no_developer_held_gemini_key() -> None:
    fields = {name.lower() for name in Settings.model_fields}
    assert not any("api_key" in name or name.startswith("gemini_key") for name in fields)


def test_sentence_service_without_stored_key_sends_no_request(
    db: Session, settings: Settings, user: User
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _ok_sentence()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    key_service = deps.get_gemini_key_service(db, deps.get_secret_cipher(settings), client)
    adapter = deps.get_sentence_service(settings, key_service, client)

    assert isinstance(adapter, GeminiAdapter)
    assert adapter.endpoint.endswith(f"/models/{settings.gemini_model}:generateContent")
    with pytest.raises(MissingGeminiKeyError):
        asyncio.run(adapter.generate(user.id, TARGET, None))
    assert requests == []


def test_sentence_service_sends_each_learners_own_key(
    db: Session, settings: Settings
) -> None:
    cipher = deps.get_secret_cipher(settings)
    alice = _make_user(db, "alice")
    bob = _make_user(db, "bob")
    alice_key, bob_key = "AIza" + "A" * 35, "AIza" + "B" * 35
    _store_key(db, cipher, alice.id, alice_key)
    _store_key(db, cipher, bob.id, bob_key)
    sent_keys: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_keys.append(request.headers.get("x-goog-api-key"))
        return _ok_sentence()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    key_service = deps.get_gemini_key_service(db, cipher, client)
    adapter = deps.get_sentence_service(settings, key_service, client)

    asyncio.run(adapter.generate(alice.id, TARGET, None))
    asyncio.run(adapter.generate(bob.id, TARGET, None))

    assert sent_keys == [alice_key, bob_key]

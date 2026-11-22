"""Integration tests for the review routes (task 9.3).

Exercises the session loop end-to-end through a FastAPI ``TestClient`` against
an isolated in-memory SQLite database, with the DB-session, current-user, and
sentence-generation dependencies overridden. The review service uses the real
key-presence check over ``gemini_credentials``. The sentence service is a stub
that never contacts Gemini.

Covers: the Gemini key check on session start (409 ``gemini_key_required``,
Req 10.1) while word routes stay usable without a key (Req 10.2); start
session (due + no-due); reveal -> rate -> advance -> summary; unknown session
-> 404; rating before reveal -> 409; invalid rating -> 422; and the Gemini BYOK
generation error mapping (Req 4.5, 4.7-4.11).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register all models on Base.metadata
from app.api import deps
from app.auth.secrets import SecretCipher
from app.db.session import Base, get_db
from app.main import create_app
from app.models.gemini_credential import GeminiCredential
from app.models.generated_sentence import GeneratedSentence as GeneratedSentenceORM
from app.models.review_record import ReviewRecord
from app.models.user import User
from app.models.word_entry import WordEntry
from app.schemas.sentence import GeneratedSentence
from app.services.review_service import ReviewService
from app.services.sentence_generation.base import (
    GenerationError,
    GenerationTimeout,
    InvalidGeminiKeyError,
    MissingGeminiKeyError,
    QuotaExceededError,
    SentenceGenerationService,
)
from app.services.study_day import current_study_day

GEMINI_KEY = "AIza" + "Q" * 31 + "wxyz"


def _sample_sentence(target_form: str) -> GeneratedSentence:
    return GeneratedSentence.model_validate(
        {
            "text": f"{target_form}を たべる",
            "tokens": [
                {
                    "surface": target_form,
                    "reading": "たべ",
                    "meaning": "to eat",
                    "is_target": True,
                    "contains_kanji": True,
                },
                {
                    "surface": "を",
                    "reading": "を",
                    "meaning": None,
                    "is_target": False,
                    "contains_kanji": False,
                },
                {
                    "surface": "ごはん",
                    "reading": "ごはん",
                    "meaning": "rice/meal",
                    "is_target": False,
                    "contains_kanji": False,
                },
            ],
        }
    )


class _StubSentenceService(SentenceGenerationService):
    """Returns a canned valid sentence; never calls a real provider."""

    def __init__(self) -> None:
        self.user_ids: list[str] = []

    async def generate(self, user_id, target, previous):  # type: ignore[override]
        self.user_ids.append(user_id)
        return _sample_sentence(target.word_form)


class _RaisingSentenceService(SentenceGenerationService):
    """Raises the given failure; the message embeds the key to prove it never leaks."""

    def __init__(self, exc_type: type[Exception]) -> None:
        self.exc_type = exc_type

    async def generate(self, user_id, target, previous):  # type: ignore[override]
        raise self.exc_type(f"provider details must not leak {GEMINI_KEY}")


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, future=True)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        ReviewService._sessions.clear()


def _make_user(db: Session, subject: str = "test-subject") -> User:
    user = User(
        email=f"{subject}@example.com",
        oidc_issuer="https://accounts.google.com",
        oidc_subject=subject,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _store_key(db: Session, user_id: str) -> None:
    cipher = SecretCipher(Fernet.generate_key())
    db.add(
        GeminiCredential(
            user_id=user_id,
            api_key_encrypted=cipher.encrypt(GEMINI_KEY),
            key_hint=GEMINI_KEY[-4:],
        )
    )
    db.commit()


@pytest.fixture()
def keyless_user(db_session: Session) -> User:
    """A Learner with no stored Gemini_API_Key."""
    return _make_user(db_session, "keyless")


@pytest.fixture()
def test_user(db_session: Session) -> User:
    """A Learner with a stored Gemini_API_Key."""
    user = _make_user(db_session)
    _store_key(db_session, user.id)
    return user


def _make_client(
    db_session: Session, user: User, sentence_service: SentenceGenerationService
) -> TestClient:
    app = create_app()

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_sentence_service] = lambda: sentence_service
    return TestClient(app)


def _add_word(
    db: Session, user_id: str, word_form: str, next_review_date: date
) -> WordEntry:
    entry = WordEntry(
        user_id=user_id,
        word_form=word_form,
        reading="よみ",
        meaning="meaning",
        next_review_date=next_review_date,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


# ---- Gemini key presence on session start (Req 10.1, 10.2) ----


def test_start_session_without_key_returns_gemini_key_required(
    db_session, keyless_user
):
    _add_word(db_session, keyless_user.id, "食べる", current_study_day())
    client = _make_client(db_session, keyless_user, _StubSentenceService())

    resp = client.post("/api/review/sessions")

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "gemini_key_required"
    assert ReviewService._sessions == {}


def test_key_check_precedes_due_selection(db_session, keyless_user):
    # Nothing is due, but the missing key is reported first.
    client = _make_client(db_session, keyless_user, _StubSentenceService())

    resp = client.post("/api/review/sessions")

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "gemini_key_required"


def test_word_routes_work_without_a_key(db_session, keyless_user):
    client = _make_client(db_session, keyless_user, _StubSentenceService())

    created = client.post(
        "/api/words",
        json={"word_form": "猫", "reading": "ねこ", "meaning": "cat"},
    )
    assert created.status_code == 201, created.text
    assert client.get("/api/words").status_code == 200


# ---- Session loop ----


def test_start_session_returns_due_words(db_session, test_user):
    _add_word(db_session, test_user.id, "食べる", current_study_day())
    client = _make_client(db_session, test_user, _StubSentenceService())

    resp = client.post("/api/review/sessions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "session_id" in body
    assert len(body["due_words"]) == 1


def test_start_session_no_due_words(db_session, test_user):
    # A reviewed word dated after the Study_Day is not due; an unreviewed word
    # would be a New_Word and selected regardless of its date (Req 8.1).
    future = _add_word(
        db_session, test_user.id, "未来", current_study_day() + timedelta(days=5)
    )
    db_session.add(ReviewRecord(word_id=future.id, rating=3, interval_days=5))
    db_session.commit()
    client = _make_client(db_session, test_user, _StubSentenceService())

    resp = client.post("/api/review/sessions")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "no_due_words"


def test_start_session_uses_the_current_study_day(
    db_session, test_user, monkeypatch
):
    # 06:30 KST on 3 June is still the 2 June Study_Day (Req 8.1).
    monkeypatch.setattr(
        "app.services.study_day._utc_now",
        lambda: datetime(2025, 6, 2, 21, 30, tzinfo=UTC),
    )
    _add_word(db_session, test_user.id, "食べる", date(2025, 6, 2))
    client = _make_client(db_session, test_user, _StubSentenceService())

    resp = client.post("/api/review/sessions")

    assert resp.status_code == 200, resp.text
    state = ReviewService._sessions[resp.json()["session_id"]]
    assert state.session_date == date(2025, 6, 2)


def test_full_flow_reveal_rate_advance_summary(db_session, test_user):
    _add_word(db_session, test_user.id, "一", current_study_day())
    _add_word(db_session, test_user.id, "二", current_study_day())
    sentence_service = _StubSentenceService()
    client = _make_client(db_session, test_user, sentence_service)

    session = client.post("/api/review/sessions").json()
    session_id = session["session_id"]
    first_word_id, second_word_id = [word["id"] for word in session["due_words"]]

    gen = client.post(
        f"/api/review/sessions/{session_id}/words/{first_word_id}/sentence"
    )
    assert gen.status_code == 200, gen.text
    assert any(t["is_target"] for t in gen.json()["tokens"])
    assert sentence_service.user_ids == [test_user.id]
    assert db_session.query(GeneratedSentenceORM).count() == 1

    assert (
        client.post(
            f"/api/review/sessions/{session_id}/words/{first_word_id}/reveal"
        ).status_code
        == 200
    )
    r1 = client.post(
        f"/api/review/sessions/{session_id}/words/{first_word_id}/rating",
        json={"rating": 3},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["session_complete"] is False
    assert r1.json()["next_word"]["id"] == second_word_id

    client.post(f"/api/review/sessions/{session_id}/words/{second_word_id}/reveal")
    r2 = client.post(
        f"/api/review/sessions/{session_id}/words/{second_word_id}/rating",
        json={"rating": 4},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["session_complete"] is True
    assert body["summary"]["total_reviewed"] == 2


def test_rating_before_reveal_is_rejected(db_session, test_user):
    w1 = _add_word(db_session, test_user.id, "一", current_study_day())
    client = _make_client(db_session, test_user, _StubSentenceService())
    session_id = client.post("/api/review/sessions").json()["session_id"]

    resp = client.post(
        f"/api/review/sessions/{session_id}/words/{w1.id}/rating",
        json={"rating": 3},
    )
    assert resp.status_code == 409, resp.text


def test_invalid_rating_rejected_at_schema_boundary(db_session, test_user):
    w1 = _add_word(db_session, test_user.id, "一", current_study_day())
    client = _make_client(db_session, test_user, _StubSentenceService())
    session_id = client.post("/api/review/sessions").json()["session_id"]
    client.post(f"/api/review/sessions/{session_id}/words/{w1.id}/reveal")

    resp = client.post(
        f"/api/review/sessions/{session_id}/words/{w1.id}/rating",
        json={"rating": 5},
    )
    # Difficulty enum rejects 5 at the Pydantic boundary -> 422.
    assert resp.status_code == 422, resp.text


def test_unknown_session_returns_404(db_session, test_user):
    client = _make_client(db_session, test_user, _StubSentenceService())
    resp = client.post("/api/review/sessions/does-not-exist/words/x/reveal")
    assert resp.status_code == 404, resp.text


# ---- Gemini BYOK generation error mapping (Req 4.5, 4.7-4.11) ----


@pytest.mark.parametrize(
    ("exc_type", "expected_status", "expected_code"),
    [
        (MissingGeminiKeyError, 409, "gemini_key_required"),
        (InvalidGeminiKeyError, 422, "invalid_gemini_key"),
        (QuotaExceededError, 429, "gemini_quota_exceeded"),
        (GenerationTimeout, 502, "generation_timeout"),
        (GenerationError, 502, "generation_failed"),
    ],
)
def test_generation_failures_return_typed_key_free_errors_and_retain_target(
    db_session, test_user, exc_type, expected_status, expected_code
):
    word = _add_word(db_session, test_user.id, "一", current_study_day())
    client = _make_client(db_session, test_user, _RaisingSentenceService(exc_type))
    session_id = client.post("/api/review/sessions").json()["session_id"]

    response = client.post(
        f"/api/review/sessions/{session_id}/words/{word.id}/sentence"
    )

    assert response.status_code == expected_status, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message"}
    assert detail["code"] == expected_code
    # Fixed message: no provider text and no key.
    assert "provider details" not in response.text
    assert GEMINI_KEY not in response.text
    assert db_session.query(GeneratedSentenceORM).count() == 0
    # The same Target_Word stays current and unrated: it can still be revealed
    # and rated.
    base = f"/api/review/sessions/{session_id}/words/{word.id}"
    assert client.post(f"{base}/reveal").status_code == 200
    rated = client.post(f"{base}/rating", json={"rating": 3})
    assert rated.status_code == 200, rated.text
    assert rated.json()["word_id"] == word.id

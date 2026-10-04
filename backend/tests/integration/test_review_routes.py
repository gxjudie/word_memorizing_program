"""Integration tests for the review routes (task 9.3).

Exercises the session loop end-to-end through a FastAPI ``TestClient`` against
an isolated in-memory SQLite database, with the DB-session, current-user, and
sentence-generation dependencies overridden. The sentence service is a mock —
the real OpenAI API is never contacted.

Covers: start session (due + no-due), reveal → rate → advance → summary,
unknown session → 404, rating before reveal → 409, invalid rating → 400, and
generation-error mapping → 502.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register all models on Base.metadata
from app.api import deps
from app.db.session import Base, get_db
from app.main import create_app
from app.models.user import User
from app.models.word_entry import WordEntry
from app.schemas.sentence import GeneratedSentence
from app.services.sentence_generation.base import (
    GenerationError,
    SentenceGenerationService,
)


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


class _MockSentenceService(SentenceGenerationService):
    """Returns a canned valid sentence; never calls a real provider."""

    async def generate(self, target, previous):  # type: ignore[override]
        return _sample_sentence(target.word_form)


class _FailingSentenceService(SentenceGenerationService):
    """Always raises GenerationError to exercise the error mapping."""

    async def generate(self, target, previous):  # type: ignore[override]
        raise GenerationError("boom")


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


@pytest.fixture()
def test_user(db_session: Session) -> User:
    user = User(email="learner@example.com", password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_client(
    db_session: Session, test_user: User, sentence_service
) -> TestClient:
    app = create_app()

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    def _override_current_user() -> User:
        return test_user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[deps.get_current_user] = _override_current_user
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


def test_start_session_returns_due_words(db_session, test_user):
    _add_word(db_session, test_user.id, "食べる", date.today())
    client = _make_client(db_session, test_user, _MockSentenceService())

    resp = client.post("/api/review/sessions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "session_id" in body
    assert len(body["due_words"]) == 1


def test_start_session_no_due_words(db_session, test_user):
    _add_word(
        db_session, test_user.id, "未来", date.today() + timedelta(days=5)
    )
    client = _make_client(db_session, test_user, _MockSentenceService())

    resp = client.post("/api/review/sessions")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "no_due_words"


def test_full_flow_reveal_rate_advance_summary(db_session, test_user):
    w1 = _add_word(db_session, test_user.id, "一", date.today())
    w2 = _add_word(db_session, test_user.id, "二", date.today())
    client = _make_client(db_session, test_user, _MockSentenceService())

    session_id = client.post("/api/review/sessions").json()["session_id"]

    # Generate a sentence for the first word (proxy succeeds).
    gen = client.post(
        f"/api/review/sessions/{session_id}/words/{w1.id}/sentence"
    )
    assert gen.status_code == 200, gen.text
    assert any(t["is_target"] for t in gen.json()["tokens"])

    # Reveal then rate the first word -> advances to the second.
    assert (
        client.post(
            f"/api/review/sessions/{session_id}/words/{w1.id}/reveal"
        ).status_code
        == 200
    )
    r1 = client.post(
        f"/api/review/sessions/{session_id}/words/{w1.id}/rating",
        json={"rating": 3},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["session_complete"] is False
    assert r1.json()["next_word"]["id"] == w2.id

    # Reveal then rate the final word -> session summary.
    client.post(f"/api/review/sessions/{session_id}/words/{w2.id}/reveal")
    r2 = client.post(
        f"/api/review/sessions/{session_id}/words/{w2.id}/rating",
        json={"rating": 4},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["session_complete"] is True
    assert body["summary"]["total_reviewed"] == 2


def test_rating_before_reveal_is_rejected(db_session, test_user):
    w1 = _add_word(db_session, test_user.id, "一", date.today())
    client = _make_client(db_session, test_user, _MockSentenceService())
    session_id = client.post("/api/review/sessions").json()["session_id"]

    resp = client.post(
        f"/api/review/sessions/{session_id}/words/{w1.id}/rating",
        json={"rating": 3},
    )
    assert resp.status_code == 409, resp.text


def test_invalid_rating_rejected_at_schema_boundary(db_session, test_user):
    w1 = _add_word(db_session, test_user.id, "一", date.today())
    client = _make_client(db_session, test_user, _MockSentenceService())
    session_id = client.post("/api/review/sessions").json()["session_id"]
    client.post(f"/api/review/sessions/{session_id}/words/{w1.id}/reveal")

    resp = client.post(
        f"/api/review/sessions/{session_id}/words/{w1.id}/rating",
        json={"rating": 5},
    )
    # Difficulty enum rejects 5 at the Pydantic boundary -> 422.
    assert resp.status_code == 422, resp.text


def test_unknown_session_returns_404(db_session, test_user):
    client = _make_client(db_session, test_user, _MockSentenceService())
    resp = client.post(
        "/api/review/sessions/does-not-exist/words/x/reveal"
    )
    assert resp.status_code == 404, resp.text


def test_generation_error_maps_to_502(db_session, test_user):
    w1 = _add_word(db_session, test_user.id, "一", date.today())
    client = _make_client(db_session, test_user, _FailingSentenceService())
    session_id = client.post("/api/review/sessions").json()["session_id"]

    resp = client.post(
        f"/api/review/sessions/{session_id}/words/{w1.id}/sentence"
    )
    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"]["code"] == "generation_failed"

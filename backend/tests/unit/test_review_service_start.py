"""Unit tests for ReviewService.start_session due selection (Req 8.1, 8.2, 8.5)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — register all models on Base.metadata
from app.db.session import Base
from app.models.user import User
from app.models.word_entry import WordEntry
from app.schemas.review import NoDueWords, ReviewSessionOut
from app.services.review_service import ReviewService, SessionState


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


def _make_user(db: Session, email: str = "learner@example.com") -> User:
    user = User(email=email, password_hash="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _add_word(
    db: Session,
    user_id: str,
    word_form: str,
    next_review_date: date,
    created_at: datetime | None = None,
) -> WordEntry:
    entry = WordEntry(
        user_id=user_id,
        word_form=word_form,
        reading="よみ",
        meaning="meaning",
        next_review_date=next_review_date,
    )
    if created_at is not None:
        entry.created_at = created_at
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def test_start_session_selects_only_non_future_words(db: Session):
    """Due = next_review_date <= today; future-dated words are excluded (Req 8.1)."""
    user = _make_user(db)
    today = date(2024, 1, 15)

    past = _add_word(db, user.id, "過去", today - timedelta(days=3))
    due_today = _add_word(db, user.id, "今日", today)
    future = _add_word(db, user.id, "未来", today + timedelta(days=1))

    result = ReviewService(db).start_session(user.id, today)

    assert isinstance(result, ReviewSessionOut)
    due_ids = {w.id for w in result.due_words}
    assert due_ids == {past.id, due_today.id}
    assert future.id not in due_ids


def test_start_session_due_words_ordered_by_creation(db: Session):
    """Due words come back in a stable, one-at-a-time presentation order (Req 8.2)."""
    user = _make_user(db)
    today = date(2024, 1, 15)

    first = _add_word(
        db, user.id, "一", today, created_at=datetime(2024, 1, 1, 9, 0, 0)
    )
    second = _add_word(
        db, user.id, "二", today, created_at=datetime(2024, 1, 2, 9, 0, 0)
    )
    third = _add_word(
        db, user.id, "三", today, created_at=datetime(2024, 1, 3, 9, 0, 0)
    )

    result = ReviewService(db).start_session(user.id, today)

    assert isinstance(result, ReviewSessionOut)
    assert [w.id for w in result.due_words] == [first.id, second.id, third.id]


def test_start_session_no_due_words_returns_marker(db: Session):
    """An empty due set returns NoDueWords and starts no session (Req 8.5)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    _add_word(db, user.id, "未来", today + timedelta(days=5))

    result = ReviewService(db).start_session(user.id, today)

    assert isinstance(result, NoDueWords)
    assert result.status == "no_due_words"


def test_start_session_empty_collection_returns_marker(db: Session):
    """A Learner with no words sees NoDueWords (Req 8.5)."""
    user = _make_user(db)
    result = ReviewService(db).start_session(user.id, date(2024, 1, 15))
    assert isinstance(result, NoDueWords)


def test_start_session_scopes_to_the_requesting_user(db: Session):
    """Only the requesting Learner's due words are selected (Req 8.1)."""
    user = _make_user(db, "a@example.com")
    other = _make_user(db, "b@example.com")
    today = date(2024, 1, 15)

    mine = _add_word(db, user.id, "私の", today)
    _add_word(db, other.id, "他の", today)

    result = ReviewService(db).start_session(user.id, today)

    assert isinstance(result, ReviewSessionOut)
    assert [w.id for w in result.due_words] == [mine.id]


def test_start_session_registers_tracked_state(db: Session):
    """A started session is registered with a one-at-a-time, unrevealed cursor."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    w1 = _add_word(
        db, user.id, "一", today, created_at=datetime(2024, 1, 1, 9, 0, 0)
    )
    w2 = _add_word(
        db, user.id, "二", today, created_at=datetime(2024, 1, 2, 9, 0, 0)
    )

    service = ReviewService(db)
    result = service.start_session(user.id, today)
    assert isinstance(result, ReviewSessionOut)

    state = service._get_session(result.session_id)
    assert isinstance(state, SessionState)
    assert state.word_ids == [w1.id, w2.id]
    assert state.current_word_id == w1.id  # exactly one current word (Req 8.2)
    assert state.revealed is False  # not yet revealed (Req 6.5 gate)
    assert state.is_complete is False
    assert state.rated_word_ids == set()

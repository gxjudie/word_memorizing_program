"""Unit tests for ReviewService.record_rating and reveal (Req 6.5, 7.4, 7.5, 8.2-8.4, 8.6).

Covers the record-rating happy path and transactional persistence, the
reveal-before-rate gate, invalid-rating rejection, one-at-a-time advance, the
end-of-session summary, and the scheduling-write-failure rollback branch.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — register all models on Base.metadata
from app.db.session import Base
from app.models.review_record import ReviewRecord
from app.models.user import User
from app.models.word_entry import WordEntry
from app.schemas.difficulty import Difficulty
from app.schemas.review import RatingResult, ReviewSessionOut
from app.services.review_service import (
    InvalidRatingError,
    NotRevealedError,
    ReviewService,
    SchedulingNotSavedError,
    WordNotCurrentError,
)


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


def _start(db: Session, user_id: str, today: date) -> tuple[ReviewService, str]:
    service = ReviewService(db)
    result = service.start_session(user_id, today)
    assert isinstance(result, ReviewSessionOut)
    return service, result.session_id


# --- Happy path + transactional persistence (Req 7.4) -----------------------


def test_record_rating_persists_schedule_in_one_transaction(db: Session):
    """A revealed rating writes a ReviewRecord and updates the word's schedule."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    word = _add_word(db, user.id, "一", today)

    service, session_id = _start(db, user.id, today)
    service.reveal(session_id, word.id)

    result = service.record_rating(user.id, session_id, word.id, Difficulty.GOOD)

    assert isinstance(result, RatingResult)
    # No history + GOOD => initial interval 1 day (Req 7.2); stored date matches.
    assert result.next_review_date == today + timedelta(days=1)

    db.refresh(word)
    assert word.next_review_date == today + timedelta(days=1)
    assert word.interval_days == 1
    assert word.repetitions == 1

    records = db.query(ReviewRecord).filter(ReviewRecord.word_id == word.id).all()
    assert len(records) == 1
    assert records[0].rating == int(Difficulty.GOOD)
    assert records[0].interval_days == 1


def test_record_rating_uses_carried_forward_history(db: Session):
    """A previously reviewed word schedules from its carried-forward state (Req 7.4)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    word = _add_word(db, user.id, "一", today)
    # Simulate prior review state: ease 2.5, interval 10 days.
    word.ease_factor = 2.5
    word.interval_days = 10
    word.repetitions = 2
    db.commit()

    service, session_id = _start(db, user.id, today)
    service.reveal(session_id, word.id)

    result = service.record_rating(user.id, session_id, word.id, Difficulty.GOOD)

    # GOOD with history => round(10 * 2.5) = 25 days.
    assert result.next_review_date == today + timedelta(days=25)
    db.refresh(word)
    assert word.interval_days == 25
    assert word.repetitions == 3


# --- Reveal gate (Req 6.5) --------------------------------------------------


def test_record_rating_requires_reveal(db: Session):
    """Rating before reveal is rejected and the word stays unrated (Req 6.5)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    word = _add_word(db, user.id, "一", today)

    service, session_id = _start(db, user.id, today)

    with pytest.raises(NotRevealedError):
        service.record_rating(user.id, session_id, word.id, Difficulty.GOOD)

    # Word stays unrated: no record, schedule unchanged, still current.
    assert db.query(ReviewRecord).count() == 0
    db.refresh(word)
    assert word.next_review_date == today
    assert word.repetitions == 0
    assert service._get_session(session_id).current_word_id == word.id


def test_reveal_rejects_non_current_word(db: Session):
    """Only the current word may be revealed (Req 8.2)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    _add_word(db, user.id, "一", today, created_at=datetime(2024, 1, 1))
    second = _add_word(db, user.id, "二", today, created_at=datetime(2024, 1, 2))

    service, session_id = _start(db, user.id, today)

    with pytest.raises(WordNotCurrentError):
        service.reveal(session_id, second.id)


# --- Invalid rating (Req 8.6) -----------------------------------------------


def test_record_rating_rejects_out_of_set_rating(db: Session):
    """A rating outside {1,2,3,4} is rejected; the word is retained (Req 8.6)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    word = _add_word(db, user.id, "一", today)

    service, session_id = _start(db, user.id, today)
    service.reveal(session_id, word.id)

    with pytest.raises(InvalidRatingError):
        service.record_rating(user.id, session_id, word.id, 5)

    assert db.query(ReviewRecord).count() == 0
    db.refresh(word)
    assert word.next_review_date == today
    assert service._get_session(session_id).current_word_id == word.id


# --- Advance (Req 8.3) ------------------------------------------------------


def test_record_rating_advances_to_next_due_word(db: Session):
    """A non-final rating advances to the next unreviewed Due_Word (Req 8.3)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    first = _add_word(db, user.id, "一", today, created_at=datetime(2024, 1, 1))
    second = _add_word(db, user.id, "二", today, created_at=datetime(2024, 1, 2))

    service, session_id = _start(db, user.id, today)
    service.reveal(session_id, first.id)

    result = service.record_rating(
        user.id, session_id, first.id, Difficulty.GOOD
    )

    assert result.session_complete is False
    assert result.next_word is not None
    assert result.next_word.id == second.id

    state = service._get_session(session_id)
    assert state.current_word_id == second.id
    assert state.revealed is False  # reveal gate resets for the new word


# --- Summary (Req 8.4) ------------------------------------------------------


def test_record_rating_final_word_produces_summary(db: Session):
    """Rating the final word yields an exact session summary (Req 8.4)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    first = _add_word(db, user.id, "一", today, created_at=datetime(2024, 1, 1))
    second = _add_word(db, user.id, "二", today, created_at=datetime(2024, 1, 2))

    service, session_id = _start(db, user.id, today)

    service.reveal(session_id, first.id)
    service.record_rating(user.id, session_id, first.id, Difficulty.GOOD)

    service.reveal(session_id, second.id)
    result = service.record_rating(
        user.id, session_id, second.id, Difficulty.VERY_EASY
    )

    assert result.session_complete is True
    assert result.summary is not None
    assert result.summary.total_reviewed == 2
    assert result.summary.counts_by_rating == {
        Difficulty.GOOD: 1,
        Difficulty.VERY_EASY: 1,
    }


# --- Scheduling-write failure (Req 7.5) -------------------------------------


def test_record_rating_write_failure_retains_schedule(db: Session):
    """A write failure rolls back and retains the previous schedule (Req 7.5)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    word = _add_word(db, user.id, "一", today)

    service, session_id = _start(db, user.id, today)
    service.reveal(session_id, word.id)

    # Inject a commit failure to simulate a transactional write error.
    original_commit = db.commit

    def _boom() -> None:
        raise RuntimeError("db down")

    db.commit = _boom  # type: ignore[method-assign]

    with pytest.raises(SchedulingNotSavedError):
        service.record_rating(user.id, session_id, word.id, Difficulty.GOOD)

    db.commit = original_commit  # type: ignore[method-assign]

    # Previous next_review_date retained; word unrated; still current.
    db.refresh(word)
    assert word.next_review_date == today
    assert word.repetitions == 0
    assert db.query(ReviewRecord).count() == 0
    assert service._get_session(session_id).current_word_id == word.id
    assert service._get_session(session_id).revealed is True

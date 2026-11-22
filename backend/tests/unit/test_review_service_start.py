"""Unit tests for ReviewService.start_session selection (Req 8.1, 8.2, 8.5, 11)."""

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


def _make_user(
    db: Session,
    email: str = "learner@example.com",
    *,
    new_word_limit: int | None = None,
    review_word_limit: int | None = None,
) -> User:
    user = User(
        email=email,
        oidc_issuer="https://accounts.google.com",
        oidc_subject=email,
    )
    if new_word_limit is not None:
        user.new_word_limit = new_word_limit
    if review_word_limit is not None:
        user.review_word_limit = review_word_limit
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


def _add_review(db: Session, word: WordEntry, *, repetitions: int = 1) -> None:
    """Give ``word`` one ReviewRecord so it is a Review_Word, not a New_Word."""
    db.add(ReviewRecord(word_id=word.id, rating=3, interval_days=1))
    word.repetitions = repetitions
    word.interval_days = 1
    db.commit()
    db.refresh(word)


def test_start_session_includes_future_dated_new_words(db: Session):
    """Unreviewed words are New_Words and are selected whatever their date (Req 8.1)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    past = _add_word(
        db, user.id, "過去", today - timedelta(days=3),
        created_at=datetime(2024, 1, 1, 9, 0, 0),
    )
    due_today = _add_word(
        db, user.id, "今日", today, created_at=datetime(2024, 1, 2, 9, 0, 0)
    )
    future = _add_word(
        db, user.id, "未来", today + timedelta(days=1),
        created_at=datetime(2024, 1, 3, 9, 0, 0),
    )

    result = ReviewService(db).start_session(user.id, today)

    assert isinstance(result, ReviewSessionOut)
    assert [w.id for w in result.due_words] == [past.id, due_today.id, future.id]


def test_start_session_review_words_first_then_new_words(db: Session):
    """Due Review_Words by (date, creation) come before New_Words (Req 8.1, 8.2)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    new_old = _add_word(
        db, user.id, "新一", today + timedelta(days=4),
        created_at=datetime(2024, 1, 1, 9, 0, 0),
    )
    rev_late = _add_word(
        db, user.id, "復一", today, created_at=datetime(2024, 1, 2, 9, 0, 0)
    )
    rev_early = _add_word(
        db, user.id, "復二", today - timedelta(days=2),
        created_at=datetime(2024, 1, 3, 9, 0, 0),
    )
    rev_future = _add_word(
        db, user.id, "復三", today + timedelta(days=1),
        created_at=datetime(2024, 1, 4, 9, 0, 0),
    )
    new_young = _add_word(
        db, user.id, "新二", today, created_at=datetime(2024, 1, 5, 9, 0, 0)
    )
    for word in (rev_late, rev_early, rev_future):
        _add_review(db, word)

    result = ReviewService(db).start_session(user.id, today)

    assert isinstance(result, ReviewSessionOut)
    assert [w.id for w in result.due_words] == [
        rev_early.id, rev_late.id, new_old.id, new_young.id,
    ]


def test_start_session_applies_both_limits(db: Session):
    """At most review_word_limit Review_Words and new_word_limit New_Words (Req 8.1)."""
    user = _make_user(db, new_word_limit=1, review_word_limit=2)
    today = date(2024, 1, 15)
    reviews = [
        _add_word(
            db, user.id, f"復{i}", today - timedelta(days=3 - i),
            created_at=datetime(2024, 1, 1, 9, 0, i),
        )
        for i in range(3)
    ]
    for word in reviews:
        _add_review(db, word)
    news = [
        _add_word(
            db, user.id, f"新{i}", today, created_at=datetime(2024, 1, 2, 9, 0, i)
        )
        for i in range(2)
    ]

    result = ReviewService(db).start_session(user.id, today)

    assert isinstance(result, ReviewSessionOut)
    assert [w.id for w in result.due_words] == [
        reviews[0].id, reviews[1].id, news[0].id,
    ]


def test_start_session_lapsed_word_with_future_date_not_selected(db: Session):
    """A lapsed word (one ReviewRecord, repetitions 0) dated after d is not selected."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    lapsed = _add_word(db, user.id, "忘れ", today + timedelta(days=1))
    _add_review(db, lapsed, repetitions=0)

    result = ReviewService(db).start_session(user.id, today)

    assert isinstance(result, NoDueWords)


def test_start_session_lapsed_word_due_selected_as_review_word(db: Session):
    """A lapsed word with next_review_date <= d is a Review_Word (Req 8.1)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    new_word = _add_word(
        db, user.id, "新", today, created_at=datetime(2024, 1, 1, 9, 0, 0)
    )
    lapsed = _add_word(
        db, user.id, "忘れ", today, created_at=datetime(2024, 1, 2, 9, 0, 0)
    )
    _add_review(db, lapsed, repetitions=0)

    result = ReviewService(db).start_session(user.id, today)

    assert isinstance(result, ReviewSessionOut)
    assert [w.id for w in result.due_words] == [lapsed.id, new_word.id]


def test_start_session_zero_limits_returns_marker(db: Session):
    """Limits 0 / 0 select nothing from a non-empty collection (Req 8.5, 11.6, 11.7)."""
    user = _make_user(db, new_word_limit=0, review_word_limit=0)
    today = date(2024, 1, 15)
    _add_word(db, user.id, "新", today)
    reviewed = _add_word(db, user.id, "復", today)
    _add_review(db, reviewed)
    before = dict(ReviewService._sessions)

    result = ReviewService(db).start_session(user.id, today)

    assert isinstance(result, NoDueWords)
    assert ReviewService._sessions == before


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
    """No due Review_Word and no New_Word returns NoDueWords (Req 8.5)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    future = _add_word(db, user.id, "未来", today + timedelta(days=5))
    _add_review(db, future)

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


def test_start_session_without_gemini_key_raises_before_due_selection(db: Session):
    """No stored key -> MissingGeminiKeyError and no session registered (Req 10.1)."""
    from app.services.sentence_generation.base import MissingGeminiKeyError

    user = _make_user(db)
    today = date(2024, 1, 15)
    _add_word(db, user.id, "今日", today)
    checked: list[str] = []

    def has_key(user_id: str) -> bool:
        checked.append(user_id)
        return False

    service = ReviewService(db, has_gemini_key=has_key)
    before = dict(ReviewService._sessions)

    with pytest.raises(MissingGeminiKeyError):
        service.start_session(user.id, today)

    assert checked == [user.id]
    assert ReviewService._sessions == before


def test_start_session_without_gemini_key_and_zero_limits_raises(db: Session):
    """The key gate runs before selection even when limits are 0 / 0 (Req 10.1)."""
    from app.services.sentence_generation.base import MissingGeminiKeyError

    user = _make_user(db, new_word_limit=0, review_word_limit=0)
    service = ReviewService(db, has_gemini_key=lambda _uid: False)

    with pytest.raises(MissingGeminiKeyError):
        service.start_session(user.id, date(2024, 1, 15))


def test_start_session_with_gemini_key_selects_due_words(db: Session):
    """With a stored key, session start proceeds to due selection (Req 10.1, 8.1)."""
    user = _make_user(db)
    today = date(2024, 1, 15)
    word = _add_word(db, user.id, "今日", today)

    result = ReviewService(db, has_gemini_key=lambda _uid: True).start_session(
        user.id, today
    )

    assert isinstance(result, ReviewSessionOut)
    assert [w.id for w in result.due_words] == [word.id]

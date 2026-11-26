"""Unit tests for Review_Session lifecycle, expiry, and ownership.

The in-memory session registry is process-wide, so these tests cover the
properties the HTTP layer relies on:

* an expired session is discarded and reported as ``SessionExpiredError``;
* a session belonging to another Learner is reported exactly like an unknown
  one, so session ids cannot be probed across accounts;
* the sentence route's ``current_word_id`` seam enforces the same checks;
* the registry is bounded and evicts the oldest sessions instead of growing
  without limit.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — register all models on Base.metadata
from app.db.session import Base
from app.models.user import User
from app.models.word_entry import WordEntry
from app.schemas.review import ReviewSessionOut
from app.services.review_service import (
    MAX_SESSIONS,
    ReviewService,
    SessionExpiredError,
    SessionNotFoundError,
)

TODAY = date(2024, 1, 15)


@pytest.fixture()
def db() -> Session:
    """An isolated in-memory SQLite session with the schema created."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, future=True
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    ReviewService._sessions.clear()  # process-wide registry: start clean
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        ReviewService._sessions.clear()


@pytest.fixture()
def clock() -> list[datetime]:
    """A one-element list holding the current fake instant."""
    return [datetime(2024, 1, 15, 9, 0, tzinfo=timezone.utc)]


def _make_user(db: Session, subject: str) -> User:
    user = User(
        email=f"{subject}@example.com",
        oidc_issuer="https://accounts.google.com",
        oidc_subject=subject,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _add_word(db: Session, user_id: str, word_form: str) -> WordEntry:
    entry = WordEntry(
        user_id=user_id,
        word_form=word_form,
        reading="よみ",
        meaning="meaning",
        next_review_date=TODAY,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _service(
    db: Session,
    clock: list[datetime],
    *,
    ttl: timedelta = timedelta(hours=2),
) -> ReviewService:
    return ReviewService(db, clock=lambda: clock[0], session_ttl=ttl)


def _start(service: ReviewService, user: User) -> tuple[str, str]:
    result = service.start_session(user.id, TODAY)
    assert isinstance(result, ReviewSessionOut)
    return result.session_id, result.due_words[0].id


def test_session_expires_after_ttl(db: Session, clock: list[datetime]) -> None:
    """A session older than the TTL is discarded and cannot be revealed."""
    user = _make_user(db, "alice")
    _add_word(db, user.id, "一")
    service = _service(db, clock)
    session_id, word_id = _start(service, user)

    clock[0] += timedelta(hours=2, seconds=1)

    with pytest.raises(SessionExpiredError):
        service.reveal(user.id, session_id, word_id)
    # Expiry removes the entry, so the next call is indistinguishable from an
    # unknown session rather than staying expired forever.
    assert session_id not in ReviewService._sessions
    with pytest.raises(SessionNotFoundError):
        service.reveal(user.id, session_id, word_id)


def test_session_is_live_just_before_ttl(db: Session, clock: list[datetime]) -> None:
    """The TTL is exclusive: a session started just now is usable."""
    user = _make_user(db, "alice")
    _add_word(db, user.id, "一")
    service = _service(db, clock)
    session_id, word_id = _start(service, user)

    clock[0] += timedelta(hours=1, minutes=59)

    service.reveal(user.id, session_id, word_id)
    assert service._get_session(session_id).revealed is True


def test_other_learners_session_is_hidden(db: Session, clock: list[datetime]) -> None:
    """A session id from another account looks exactly like an unknown one."""
    owner = _make_user(db, "alice")
    intruder = _make_user(db, "bob")
    _add_word(db, owner.id, "一")
    service = _service(db, clock)
    session_id, word_id = _start(service, owner)

    with pytest.raises(SessionNotFoundError):
        service.reveal(intruder.id, session_id, word_id)
    with pytest.raises(SessionNotFoundError):
        service.current_word_id(intruder.id, session_id)
    with pytest.raises(SessionNotFoundError):
        service.record_rating(intruder.id, session_id, word_id, 3)

    # The owner is unaffected.
    service.reveal(owner.id, session_id, word_id)


def test_current_word_id_requires_ownership(db: Session, clock: list[datetime]) -> None:
    """The sentence route's seam returns the current word only to its owner."""
    user = _make_user(db, "alice")
    _add_word(db, user.id, "一")
    service = _service(db, clock)
    session_id, word_id = _start(service, user)

    assert service.current_word_id(user.id, session_id) == word_id
    with pytest.raises(SessionNotFoundError):
        service.current_word_id("someone-else", session_id)


def test_current_word_id_is_none_once_complete(
    db: Session, clock: list[datetime]
) -> None:
    """A fully rated session exposes no current word."""
    user = _make_user(db, "alice")
    _add_word(db, user.id, "一")
    service = _service(db, clock)
    session_id, word_id = _start(service, user)

    service.reveal(user.id, session_id, word_id)
    result = service.record_rating(user.id, session_id, word_id, 3)

    assert result.session_complete is True
    with pytest.raises(SessionNotFoundError):
        service.current_word_id(user.id, session_id)


def test_registry_is_bounded(db: Session, clock: list[datetime]) -> None:
    """Starting more than MAX_SESSIONS drops the oldest, not the newest."""
    user = _make_user(db, "alice")
    _add_word(db, user.id, "一")
    service = _service(db, clock)

    session_ids = [_start(service, user)[0] for _ in range(MAX_SESSIONS + 5)]

    assert len(ReviewService._sessions) == MAX_SESSIONS
    assert session_ids[0] not in ReviewService._sessions  # oldest evicted
    assert session_ids[-1] in ReviewService._sessions  # newest retained

"""Example tests for the Study_Day helper and its manual-add call site (task 19.1).

Covers the 07:00 KST boundary examples from the design (Req 1.5, 8.1) and
``WordService.add_word`` defaulting to ``current_study_day()`` (Req 1.5).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 - register all models on Base.metadata
from app.db.session import Base
from app.models.user import User
from app.schemas.word import WordInput
from app.services.study_day import current_study_day, study_day
from app.services.word_service import WordService

KST = timezone(timedelta(hours=9))

# 06:30 KST on 3 June 2025, still the 2 June Study_Day.
EARLY_MORNING_KST = datetime(2025, 6, 2, 21, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2025, 6, 2, 21, 59, 59, 999999, tzinfo=UTC), date(2025, 6, 2)),
        (datetime(2025, 6, 2, 22, 0, 0, tzinfo=UTC), date(2025, 6, 3)),
        (datetime(2025, 6, 3, 6, 59, 59, tzinfo=KST), date(2025, 6, 2)),
        (datetime(2025, 6, 3, 7, 0, 0, tzinfo=KST), date(2025, 6, 3)),
        (datetime(2025, 6, 2, 22, 0, 0), date(2025, 6, 3)),  # naive is read as UTC
    ],
    ids=[
        "utc-just-before-0700-kst",
        "utc-at-0700-kst",
        "kst-0659",
        "kst-0700",
        "naive-utc-at-0700-kst",
    ],
)
def test_study_day_boundary_examples(instant: datetime, expected: date) -> None:
    assert study_day(instant) == expected


def test_current_study_day_uses_the_injected_clock() -> None:
    assert current_study_day(lambda: EARLY_MORNING_KST) == date(2025, 6, 2)


def test_current_study_day_reads_the_patched_utc_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.study_day._utc_now", lambda: EARLY_MORNING_KST)
    assert current_study_day() == date(2025, 6, 2)


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_add_word_without_today_uses_the_current_study_day(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.study_day._utc_now", lambda: EARLY_MORNING_KST)
    user = User(
        email="learner@example.com",
        oidc_issuer="https://accounts.google.com",
        oidc_subject="learner",
    )
    db.add(user)
    db.commit()

    entry = WordService(db).add_word(
        user.id, WordInput(word_form="猫", reading="ねこ", meaning="cat")
    )

    assert entry.next_review_date == date(2025, 6, 2)

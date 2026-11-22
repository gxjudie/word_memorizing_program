"""Unit tests for StudySettingsService and the study-settings schemas (task 19.6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 -- register all ORM models
from app.db.session import Base
from app.models.user import User
from app.schemas.study_settings import StudySettingsIn, StudySettingsOut
from app.services.study_settings_service import StudySettingsService, UserNotFoundError


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


def _make_user(db: Session, subject: str) -> User:
    learner = User(
        email=f"{subject}@example.com",
        oidc_issuer="https://accounts.google.com",
        oidc_subject=subject,
    )
    db.add(learner)
    db.commit()
    db.refresh(learner)
    return learner


# ---- schemas -----------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 50, 1000])
def test_in_accepts_integers_in_range(value: int) -> None:
    data = StudySettingsIn(new_word_limit=value, review_word_limit=value)
    assert data.new_word_limit == value
    assert data.review_word_limit == value


@pytest.mark.parametrize("bad", [-1, 1001, True, False, 5.0, 5.5, "5", None])
def test_in_rejects_non_integer_or_out_of_range(bad: object) -> None:
    with pytest.raises(ValidationError) as exc:
        StudySettingsIn(new_word_limit=bad, review_word_limit=10)
    assert [e["loc"] for e in exc.value.errors()] == [("new_word_limit",)]


def test_in_reports_each_invalid_field() -> None:
    with pytest.raises(ValidationError) as exc:
        StudySettingsIn(new_word_limit="x", review_word_limit=2000)
    assert {e["loc"] for e in exc.value.errors()} == {
        ("new_word_limit",),
        ("review_word_limit",),
    }


@pytest.mark.parametrize(
    "payload", [{}, {"new_word_limit": 1}, {"review_word_limit": 1}]
)
def test_in_requires_both_fields(payload: dict) -> None:
    with pytest.raises(ValidationError):
        StudySettingsIn.model_validate(payload)


def test_in_rejects_float_and_string_from_json() -> None:
    with pytest.raises(ValidationError):
        StudySettingsIn.model_validate_json('{"new_word_limit": 5.0, "review_word_limit": 1}')
    with pytest.raises(ValidationError):
        StudySettingsIn.model_validate_json('{"new_word_limit": "5", "review_word_limit": 1}')


def test_out_reads_from_attributes() -> None:
    user = User(new_word_limit=7, review_word_limit=8)
    assert StudySettingsOut.model_validate(user) == StudySettingsOut(
        new_word_limit=7, review_word_limit=8
    )


# ---- service -----------------------------------------------------------------


def test_get_returns_defaults_for_new_user(db: Session) -> None:
    user = _make_user(db, "a")
    out = StudySettingsService(db).get(user.id)
    assert out == StudySettingsOut(new_word_limit=50, review_word_limit=100)


def test_update_stores_both_values_and_get_reads_them(db: Session) -> None:
    user = _make_user(db, "a")
    service = StudySettingsService(db)
    out = service.update(user.id, StudySettingsIn(new_word_limit=0, review_word_limit=1000))
    assert out == StudySettingsOut(new_word_limit=0, review_word_limit=1000)

    db.expire_all()
    assert service.get(user.id) == out


def test_update_touches_only_the_given_user(db: Session) -> None:
    a = _make_user(db, "a")
    b = _make_user(db, "b")
    service = StudySettingsService(db)
    service.update(a.id, StudySettingsIn(new_word_limit=3, review_word_limit=4))

    db.expire_all()
    assert service.get(b.id) == StudySettingsOut(new_word_limit=50, review_word_limit=100)


def test_unknown_user_raises(db: Session) -> None:
    service = StudySettingsService(db)
    with pytest.raises(UserNotFoundError):
        service.get("missing")
    with pytest.raises(UserNotFoundError):
        service.update("missing", StudySettingsIn(new_word_limit=1, review_word_limit=1))


def test_update_rolls_back_and_reraises_on_database_error(db: Session) -> None:
    user = _make_user(db, "a")
    # A real database error: SQLite aborts every UPDATE on users.
    db.execute(
        text(
            "CREATE TRIGGER block_user_update BEFORE UPDATE ON users "
            "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
        )
    )
    db.commit()

    service = StudySettingsService(db)
    with pytest.raises(SQLAlchemyError):
        service.update(user.id, StudySettingsIn(new_word_limit=1, review_word_limit=2))

    # The session is usable again and the stored values are unchanged.
    assert service.get(user.id) == StudySettingsOut(new_word_limit=50, review_word_limit=100)

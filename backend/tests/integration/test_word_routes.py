"""Integration tests for the Word_Collection routes (task 9.2, Req 1.1-1.5).

Exercises ``POST /api/words`` and ``GET /api/words`` end-to-end through a
FastAPI ``TestClient`` against an isolated in-memory SQLite database, with the
authenticated-user and DB-session dependencies overridden so the tests do not
depend on the real database or a live auth flow.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Import every model so they register on Base.metadata before create_all.
from app.api import deps
from app.db.session import Base, get_db
from app.main import create_app
from app.models.generated_sentence import GeneratedSentence  # noqa: F401
from app.models.review_record import ReviewRecord  # noqa: F401
from app.models.user import User
from app.models.word_entry import WordEntry  # noqa: F401


@pytest.fixture()
def db_session() -> Iterator[Session]:
    """Provide an isolated in-memory SQLite session with all tables created."""
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
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def test_user(db_session: Session) -> User:
    """Create and persist a Learner to own the collection."""
    user = User(email="learner@example.com", password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def client(db_session: Session, test_user: User) -> Iterator[TestClient]:
    """A TestClient with DB-session and current-user dependencies overridden."""
    app = create_app()

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    def _override_current_user() -> User:
        return test_user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[deps.get_current_user] = _override_current_user

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


VALID_WORD = {"word_form": "食べる", "reading": "たべる", "meaning": "to eat"}


def test_add_valid_word_returns_created_record(client: TestClient) -> None:
    """A valid word is stored faithfully and returned as due today (Req 1.1, 1.5)."""
    resp = client.post("/api/words", json=VALID_WORD)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["word_form"] == VALID_WORD["word_form"]
    assert data["reading"] == VALID_WORD["reading"]
    assert data["meaning"] == VALID_WORD["meaning"]
    assert data["id"]
    # Due immediately: next_review_date is set on creation (Req 1.5).
    assert data["next_review_date"]


def test_duplicate_word_returns_409(client: TestClient) -> None:
    """A duplicate word form is rejected with 409 (Req 1.4)."""
    first = client.post("/api/words", json=VALID_WORD)
    assert first.status_code == 201, first.text

    dup = client.post("/api/words", json=VALID_WORD)
    assert dup.status_code == 409, dup.text

    # The existing entry is untouched: the collection still has exactly one word.
    listing = client.get("/api/words")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_invalid_fields_return_400_naming_each_field(client: TestClient) -> None:
    """Each offending field is named in a 400 response (Req 1.2, 1.3, Property 2)."""
    bad = {
        "word_form": "   ",  # whitespace-only (Req 1.2)
        "reading": "",  # empty (Req 1.2)
        "meaning": "x" * 501,  # over-length (Req 1.3)
    }
    resp = client.post("/api/words", json=bad)

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    named = {err["field"] for err in detail["errors"]}
    assert named == {"word_form", "reading", "meaning"}


def test_missing_single_field_names_only_that_field(client: TestClient) -> None:
    """A single missing field is the only one named (Property 2)."""
    bad = {"reading": "たべる", "meaning": "to eat"}  # word_form missing
    resp = client.post("/api/words", json=bad)

    assert resp.status_code == 400, resp.text
    named = {err["field"] for err in resp.json()["detail"]["errors"]}
    assert named == {"word_form"}


def test_list_returns_collection_in_order(client: TestClient) -> None:
    """GET /api/words returns the Learner's collection."""
    assert client.get("/api/words").json() == []

    client.post("/api/words", json=VALID_WORD)
    client.post(
        "/api/words",
        json={"word_form": "飲む", "reading": "のむ", "meaning": "to drink"},
    )

    listing = client.get("/api/words")
    assert listing.status_code == 200
    forms = [w["word_form"] for w in listing.json()]
    assert forms == ["食べる", "飲む"]

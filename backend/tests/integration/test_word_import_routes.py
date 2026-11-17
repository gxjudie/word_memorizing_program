"""Integration tests for the Anki import routes (task 17.18, Req 2).

Requests go through a FastAPI ``TestClient`` with real App_Session bearer
tokens and an in-memory SQLite database. The preview store is a test instance
with its own temp root and a controllable clock, injected through
``get_preview_store``. Every deck is built in code by
``tests/fixtures/anki_builders.py``. No Gemini key is stored in any test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 -- register all ORM models
from app.api import deps
from app.auth.session import issue_app_session, revoke_app_session
from app.config import Settings, get_settings
from app.db.session import Base, get_db
from app.main import create_app
from app.models.gemini_credential import GeminiCredential
from app.models.review_record import ReviewRecord
from app.models.user import User
from app.models.word_entry import WordEntry
from app.services.anki_import.preview_store import TEMP_PREFIX, PreviewStore
from tests.fixtures.anki_builders import (
    NoteSpec,
    NoteTypeSpec,
    build_anki21b_apkg,
    build_text_export,
)

PREVIEW_PATH = "/api/words/import/preview"

TEXT_ROWS = [
    ["食べる", "たべる", "to eat"],
    ["飲[の]む", "", "to drink"],  # reading falls back to the furigana (Req 2.25 b)
    ["", "から", "empty word"],  # missing field (Req 2.27)
    ["食べる", "たべる", "duplicate"],  # duplicate within the import (Req 2.29)
]
TEXT_DECK = build_text_export(TEXT_ROWS, headers=[("columns", "Word\tReading\tMeaning")])

JAPANESE = NoteTypeSpec(id=100, name="Japanese", field_names=("Expression", "Reading", "Meaning"))
BASIC = NoteTypeSpec(id=200, name="Basic", field_names=("Front", "Back"))
APKG_DECK = build_anki21b_apkg(
    [JAPANESE, BASIC],
    [
        NoteSpec(id=3, mid=100, values=("学校", "がっこう", "school")),
        NoteSpec(id=1, mid=100, values=("<b>猫</b>", "ねこ", "cat")),
        NoteSpec(id=2, mid=100, values=("犬", "いぬ", "dog&amp;hound")),
        NoteSpec(id=5, mid=200, values=("水[みず]", "water")),
        NoteSpec(id=4, mid=200, values=("火", "fire")),
    ],
)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


# ---- fixtures ---------------------------------------------------------------------


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def settings() -> Settings:
    return Settings(db_url="sqlite://", session_secret="word-import-route-test-secret")


def _make_user(db: Session, subject: str) -> User:
    user = User(
        email=f"{subject}@example.com",
        name=subject,
        oidc_issuer="https://accounts.google.com",
        oidc_subject=subject,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def user(db_session: Session) -> User:
    return _make_user(db_session, "learner")


@pytest.fixture()
def other_user(db_session: Session) -> User:
    return _make_user(db_session, "other")


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def temp_root(tmp_path: Path) -> Path:
    root = tmp_path / "tmp-root"
    root.mkdir()
    return root


@pytest.fixture()
def store(clock: FakeClock, temp_root: Path) -> PreviewStore:
    return PreviewStore(clock=clock, temp_root=temp_root)


@pytest.fixture()
def client(
    db_session: Session, settings: Settings, store: PreviewStore
) -> Iterator[TestClient]:
    application = create_app()

    def _override_db() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_db] = _override_db
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[deps.get_preview_store] = lambda: store
    with TestClient(application) as test_client:
        yield test_client


def _auth_for(settings: Settings, user: User) -> dict[str, str]:
    session = issue_app_session(user.id, secret=settings.session_secret)
    return {"Authorization": f"Bearer {session.token}"}


@pytest.fixture()
def auth(settings: Settings, user: User) -> dict[str, str]:
    return _auth_for(settings, user)


@pytest.fixture()
def other_auth(settings: Settings, other_user: User) -> dict[str, str]:
    return _auth_for(settings, other_user)


# ---- helpers ----------------------------------------------------------------------


def _upload(client: TestClient, headers: dict[str, str], name: str, data: bytes):
    return client.post(
        PREVIEW_PATH,
        files={"file": (name, data, "application/octet-stream")},
        headers=headers,
    )


def _words(db: Session, user: User) -> list[WordEntry]:
    db.expire_all()
    return list(
        db.execute(
            select(WordEntry).where(WordEntry.user_id == user.id).order_by(WordEntry.word_form)
        ).scalars()
    )


def _workdirs(root: Path) -> list[Path]:
    return [p for p in root.iterdir() if p.name.startswith(TEMP_PREFIX)]


def _assert_coded(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == code
    assert isinstance(detail["message"], str) and detail["message"]
    return detail


# ---- end to end -------------------------------------------------------------------


def test_text_export_preview_confirm_summary(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    user: User,
    other_user: User,
    temp_root: Path,
) -> None:
    resp = _upload(client, auth, "deck.txt", TEXT_DECK)

    assert resp.status_code == 201, resp.text
    preview = resp.json()
    assert preview["file_kind"] == "text"
    assert preview["note_types"] == []
    assert preview["selected_note_type_id"] is None
    assert preview["fields"] == [
        {"index": 0, "name": "Word"},
        {"index": 1, "name": "Reading"},
        {"index": 2, "name": "Meaning"},
    ]
    assert preview["suggested_mapping"] == {"word": 0, "reading": 1, "meaning": 2}
    assert preview["note_count"] == 4
    assert preview["samples"] == TEXT_ROWS
    assert preview["expires_at"]
    assert _workdirs(temp_root) == []  # deleted as soon as parsing ended (Req 2.40)
    assert _words(db_session, user) == []  # nothing written before confirm (Req 2.4)

    confirm = client.post(
        f"{PREVIEW_PATH}/{preview['preview_id']}/confirm",
        json={"mapping": preview["suggested_mapping"]},
        headers=auth,
    )

    assert confirm.status_code == 200, confirm.text
    assert confirm.json() == {
        "created": 2,
        "skipped_missing_field": 1,
        "skipped_duplicate": 1,
        "skipped_invalid_length": 0,
        "skipped": [
            {"word_form": None, "reason": "missing_field"},
            {"word_form": "食べる", "reason": "duplicate"},
        ],
    }
    stored = {(w.word_form, w.reading, w.meaning) for w in _words(db_session, user)}
    assert stored == {("食べる", "たべる", "to eat"), ("飲む", "のむ", "to drink")}
    assert _words(db_session, other_user) == []
    assert db_session.execute(select(ReviewRecord)).first() is None
    assert db_session.execute(select(GeminiCredential)).first() is None

    again = client.post(
        f"{PREVIEW_PATH}/{preview['preview_id']}/confirm",
        json={"mapping": preview["suggested_mapping"]},
        headers=auth,
    )
    _assert_coded(again, 410, "preview_expired")


def test_apkg_preview_switch_note_type_and_confirm(
    client: TestClient, auth: dict[str, str], db_session: Session, user: User
) -> None:
    resp = _upload(client, auth, "Deck.APKG", APKG_DECK)

    assert resp.status_code == 201, resp.text
    preview = resp.json()
    assert preview["file_kind"] == "apkg"
    assert sorted(preview["note_types"], key=lambda t: t["id"]) == [
        {"id": "100", "name": "Japanese", "note_count": 3},
        {"id": "200", "name": "Basic", "note_count": 2},
    ]
    assert preview["selected_note_type_id"] == "100"  # most notes (Req 2.8)
    assert [f["name"] for f in preview["fields"]] == ["Expression", "Reading", "Meaning"]
    # Ascending note id order, cleaned values (Req 2.5).
    assert preview["samples"] == [
        ["猫", "ねこ", "cat"],
        ["犬", "いぬ", "dog&hound"],
        ["学校", "がっこう", "school"],
    ]

    switched = client.get(
        f"{PREVIEW_PATH}/{preview['preview_id']}",
        params={"note_type_id": "200"},
        headers=auth,
    )
    assert switched.status_code == 200, switched.text
    basic = switched.json()
    assert basic["selected_note_type_id"] == "200"
    assert [f["name"] for f in basic["fields"]] == ["Front", "Back"]
    assert basic["suggested_mapping"] == {"word": 0, "reading": None, "meaning": 1}
    assert basic["note_count"] == 2
    assert basic["samples"] == [["火", "fire"], ["水[みず]", "water"]]

    bad_type = client.get(
        f"{PREVIEW_PATH}/{preview['preview_id']}",
        params={"note_type_id": "999"},
        headers=auth,
    )
    _assert_coded(bad_type, 400, "invalid_note_type")

    confirm = client.post(
        f"{PREVIEW_PATH}/{preview['preview_id']}/confirm",
        json={"note_type_id": "200", "mapping": basic["suggested_mapping"]},
        headers=auth,
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["created"] == 2
    stored = {(w.word_form, w.reading, w.meaning) for w in _words(db_session, user)}
    assert stored == {("火", "火", "fire"), ("水", "みず", "water")}


# ---- rejection --------------------------------------------------------------------


def test_colpkg_is_rejected_before_the_body_is_stored(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    user: User,
    temp_root: Path,
) -> None:
    resp = _upload(client, auth, "collection.COLPKG", APKG_DECK)

    detail = _assert_coded(resp, 415, "unsupported_colpkg")
    assert set(detail) == {"code", "message"}
    assert _workdirs(temp_root) == []
    assert _words(db_session, user) == []


@pytest.mark.parametrize(
    ("name", "data", "status", "code"),
    [
        ("deck.zip", b"PK\x03\x04", 415, "unsupported_extension"),
        ("deck.apkg", b"not a zip at all", 400, "unreadable_file"),
        ("deck.txt", b"\xff\xfe broken", 400, "invalid_text_export"),
        ("deck.tsv", b"#columns:a\tb\n", 400, "no_notes"),
    ],
)
def test_bad_files_get_their_code_and_leave_nothing_behind(
    client: TestClient,
    auth: dict[str, str],
    temp_root: Path,
    name: str,
    data: bytes,
    status: int,
    code: str,
) -> None:
    resp = _upload(client, auth, name, data)

    _assert_coded(resp, status, code)
    assert _workdirs(temp_root) == []


def test_upload_without_a_file_part_is_unreadable(
    client: TestClient, auth: dict[str, str]
) -> None:
    resp = client.post(PREVIEW_PATH, files={"other": ("x.txt", b"a\tb\n")}, headers=auth)

    _assert_coded(resp, 400, "unreadable_file")


# ---- authentication ---------------------------------------------------------------


def _all_routes(client: TestClient, headers: dict[str, str], preview_id: str):
    return [
        _upload(client, headers, "deck.txt", TEXT_DECK),
        client.get(f"{PREVIEW_PATH}/{preview_id}", headers=headers),
        client.post(
            f"{PREVIEW_PATH}/{preview_id}/confirm",
            json={"mapping": {"word": 0, "reading": 1, "meaning": 2}},
            headers=headers,
        ),
        client.delete(f"{PREVIEW_PATH}/{preview_id}", headers=headers),
    ]


@pytest.mark.parametrize("kind", ["missing", "invalid", "revoked"])
def test_every_route_requires_an_app_session(
    client: TestClient,
    auth: dict[str, str],
    settings: Settings,
    db_session: Session,
    user: User,
    temp_root: Path,
    kind: str,
) -> None:
    preview_id = _upload(client, auth, "deck.txt", TEXT_DECK).json()["preview_id"]
    if kind == "missing":
        headers: dict[str, str] = {}
    elif kind == "invalid":
        headers = {"Authorization": "Bearer not-a-session"}
    else:
        session = issue_app_session(user.id, secret=settings.session_secret)
        revoke_app_session(db_session, session.session_id, session.expires_at)
        headers = {"Authorization": f"Bearer {session.token}"}

    for resp in _all_routes(client, headers, preview_id):
        assert resp.status_code == 401, resp.text
        assert resp.json() == {"detail": "Not authenticated"}

    assert _workdirs(temp_root) == []
    assert _words(db_session, user) == []
    # The owner's preview is untouched by the rejected cancel and confirm.
    assert client.get(f"{PREVIEW_PATH}/{preview_id}", headers=auth).status_code == 200


# ---- preview ownership, expiry, cancel, mapping -------------------------------------


def test_another_account_cannot_read_confirm_or_cancel_a_preview(
    client: TestClient,
    auth: dict[str, str],
    other_auth: dict[str, str],
    db_session: Session,
    user: User,
    other_user: User,
) -> None:
    preview = _upload(client, auth, "deck.txt", TEXT_DECK).json()
    url = f"{PREVIEW_PATH}/{preview['preview_id']}"
    body = {"mapping": preview["suggested_mapping"]}

    _assert_coded(client.get(url, headers=other_auth), 410, "preview_expired")
    _assert_coded(
        client.post(f"{url}/confirm", json=body, headers=other_auth), 410, "preview_expired"
    )
    assert client.delete(url, headers=other_auth).status_code == 204
    assert _words(db_session, other_user) == []

    owner = client.post(f"{url}/confirm", json=body, headers=auth)
    assert owner.status_code == 200, owner.text
    assert owner.json()["created"] == 2
    assert _words(db_session, other_user) == []


def test_expired_preview_is_rejected(
    client: TestClient,
    auth: dict[str, str],
    clock: FakeClock,
    db_session: Session,
    user: User,
) -> None:
    preview = _upload(client, auth, "deck.txt", TEXT_DECK).json()
    url = f"{PREVIEW_PATH}/{preview['preview_id']}"

    clock.now += timedelta(minutes=30)

    _assert_coded(client.get(url, headers=auth), 410, "preview_expired")
    confirm = client.post(
        f"{url}/confirm", json={"mapping": preview["suggested_mapping"]}, headers=auth
    )
    _assert_coded(confirm, 410, "preview_expired")
    assert _words(db_session, user) == []


def test_cancel_is_idempotent_and_discards_the_preview(
    client: TestClient, auth: dict[str, str], db_session: Session, user: User
) -> None:
    preview = _upload(client, auth, "deck.txt", TEXT_DECK).json()
    url = f"{PREVIEW_PATH}/{preview['preview_id']}"

    first = client.delete(url, headers=auth)
    second = client.delete(url, headers=auth)

    assert (first.status_code, second.status_code) == (204, 204)
    assert first.content == b""
    confirm = client.post(
        f"{url}/confirm", json={"mapping": preview["suggested_mapping"]}, headers=auth
    )
    _assert_coded(confirm, 410, "preview_expired")
    assert _words(db_session, user) == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {"mapping": {"word": 0, "reading": None, "meaning": 0}},
            {"word": "duplicate", "meaning": "duplicate"},
        ),
        (
            {"mapping": {"word": None, "reading": 5, "meaning": 2}},
            {"word": "missing", "reading": "out_of_range"},
        ),
        (
            # Non-integer role values (a string, a bool, a float) are malformed.
            {"mapping": {"word": "0", "reading": True, "meaning": 2.0}},
            {"word": "missing", "reading": "out_of_range", "meaning": "missing"},
        ),
        (["not", "an", "object"], {"word": "missing", "meaning": "missing"}),
    ],
)
def test_invalid_mapping_names_each_role_and_keeps_the_preview(
    client: TestClient,
    auth: dict[str, str],
    db_session: Session,
    user: User,
    body: object,
    expected: dict[str, str],
) -> None:
    preview = _upload(client, auth, "deck.txt", TEXT_DECK).json()
    url = f"{PREVIEW_PATH}/{preview['preview_id']}"

    resp = client.post(f"{url}/confirm", json=body, headers=auth)

    detail = _assert_coded(resp, 422, "invalid_mapping")
    assert {e["field"]: e["reason"] for e in detail["errors"]} == expected
    assert all(e["message"] for e in detail["errors"])
    assert _words(db_session, user) == []
    # The preview stays pending and can still be confirmed (Req 2.22).
    ok = client.post(
        f"{url}/confirm", json={"mapping": preview["suggested_mapping"]}, headers=auth
    )
    assert ok.status_code == 200, ok.text


def test_invalid_json_body_is_an_invalid_mapping(
    client: TestClient, auth: dict[str, str]
) -> None:
    preview = _upload(client, auth, "deck.txt", TEXT_DECK).json()

    resp = client.post(
        f"{PREVIEW_PATH}/{preview['preview_id']}/confirm",
        content=b"{not json",
        headers={**auth, "Content-Type": "application/json"},
    )

    detail = _assert_coded(resp, 422, "invalid_mapping")
    assert {e["field"] for e in detail["errors"]} == {"word", "meaning"}


def test_confirm_while_the_account_import_lock_is_held_is_409(
    client: TestClient,
    auth: dict[str, str],
    store: PreviewStore,
    user: User,
    db_session: Session,
) -> None:
    running = _upload(client, auth, "deck.txt", TEXT_DECK).json()

    with store.begin_import(running["preview_id"], user.id):
        # The first preview is IMPORTING, so this upload does not supersede it.
        waiting = _upload(client, auth, "deck.txt", TEXT_DECK).json()
        url = f"{PREVIEW_PATH}/{waiting['preview_id']}/confirm"
        body = {"mapping": waiting["suggested_mapping"]}
        resp = client.post(url, json=body, headers=auth)
        _assert_coded(resp, 409, "import_in_progress")
        assert _words(db_session, user) == []

    # The rejected preview stayed pending and is confirmable now (Req 2.42).
    later = client.post(url, json=body, headers=auth)
    assert later.status_code == 200, later.text
    assert later.json()["created"] == 2


def test_startup_removes_stale_workdirs(temp_root: Path) -> None:
    stale = temp_root / f"{TEMP_PREFIX}stale"
    fresh = temp_root / f"{TEMP_PREFIX}fresh"
    unrelated = temp_root / "other-stale"
    for path in (stale, fresh, unrelated):
        path.mkdir()
    old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(stale, (old, old))
    os.utime(unrelated, (old, old))
    real_clock_store = PreviewStore(temp_root=temp_root)
    application = create_app()
    application.dependency_overrides[deps.get_preview_store] = lambda: real_clock_store

    with TestClient(application):
        pass

    assert not stale.exists()
    assert fresh.exists() and unrelated.exists()

"""Integration tests for ``/api/gemini-key`` and the key-safe 422 handler (task 9.5).

Requests go through a FastAPI ``TestClient`` with real App_Session bearer
tokens, an in-memory SQLite database, and a Fernet ``TOKEN_ENCRYPTION_KEY``.
Gemini is mocked with ``httpx.MockTransport``; no test reaches the network.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

import httpx
import pytest
from cryptography.fernet import Fernet
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
from app.models.user import User
from app.services import gemini_key_service

KEY_A = "AIza" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6_-Q"
KEY_B = "AIza" + "z" * 31 + "WXYZ"
KEY_AQ = "AQ." + "N4m5P6q7" * 5 + "WXYZ"  # new-style "AQ." key (47 chars)
assert len(KEY_A) == 39 and len(KEY_B) == 39

Handler = Callable[[httpx.Request], httpx.Response]


class FakeGemini:
    """Records every request; responds with the configured handler."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler: Handler = lambda _req: httpx.Response(200, json={"models": []})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _rpc_error(status: int, reason: str, rpc_status: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "error": {
                "code": status,
                "message": "provider message",
                "status": rpc_status,
                "details": [{"reason": reason}],
            }
        },
    )


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
    return Settings(
        db_url="sqlite://",
        session_secret="gemini-key-route-test-secret",
        token_encryption_key=Fernet.generate_key().decode("ascii"),
    )


@pytest.fixture()
def user(db_session: Session) -> User:
    learner = User(
        email="learner@example.com",
        name="Learner",
        oidc_issuer="https://accounts.google.com",
        oidc_subject="google-subject",
    )
    db_session.add(learner)
    db_session.commit()
    db_session.refresh(learner)
    return learner


@pytest.fixture()
def gemini() -> FakeGemini:
    return FakeGemini()


@pytest.fixture()
def client(
    db_session: Session, settings: Settings, gemini: FakeGemini
) -> Iterator[TestClient]:
    application = create_app()
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(gemini))

    def _override_db() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_db] = _override_db
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[deps.get_gemini_http_client] = lambda: http_client
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture()
def auth(settings: Settings, user: User) -> dict[str, str]:
    session = issue_app_session(user.id, secret=settings.session_secret)
    return {"Authorization": f"Bearer {session.token}"}


def _stored(db: Session, user: User) -> GeminiCredential | None:
    db.expire_all()
    return db.execute(
        select(GeminiCredential).where(GeminiCredential.user_id == user.id)
    ).scalar_one_or_none()


def _connect(client: TestClient, auth: dict[str, str], key: str) -> httpx.Response:
    return client.put("/api/gemini-key", json={"api_key": key}, headers=auth)


# ---- authentication -----------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_routes_require_an_app_session(
    client: TestClient, gemini: FakeGemini, method: str
) -> None:
    resp = client.request(method, "/api/gemini-key", json={"api_key": KEY_A})

    assert resp.status_code == 401
    assert gemini.requests == []


def test_revoked_session_is_rejected(
    client: TestClient, db_session: Session, settings: Settings, user: User
) -> None:
    session = issue_app_session(user.id, secret=settings.session_secret)
    revoke_app_session(db_session, session.session_id, session.expires_at)

    resp = client.get(
        "/api/gemini-key", headers={"Authorization": f"Bearer {session.token}"}
    )

    assert resp.status_code == 401
    assert "connected" not in resp.text


def test_missing_encryption_key_returns_503(
    client: TestClient, settings: Settings, auth: dict[str, str], gemini: FakeGemini
) -> None:
    settings.token_encryption_key = ""

    for method in ("GET", "PUT", "DELETE"):
        resp = client.request(method, "/api/gemini-key", json={"api_key": KEY_A}, headers=auth)
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "key_storage_unavailable"
    assert gemini.requests == []


# ---- connect / status / remove --------------------------------------------------


def test_status_is_not_connected_initially(client: TestClient, auth: dict[str, str]) -> None:
    resp = client.get("/api/gemini-key", headers=auth)

    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "key_hint": None}


def test_valid_key_is_verified_once_and_stored_encrypted(
    client: TestClient,
    auth: dict[str, str],
    gemini: FakeGemini,
    db_session: Session,
    user: User,
) -> None:
    resp = _connect(client, auth, f"  {KEY_A}\n")

    assert resp.status_code == 200
    assert resp.json() == {
        "connected": True,
        "key_hint": KEY_A[-4:],
        "quota_exhausted_notice": False,
    }
    assert KEY_A not in resp.text
    assert len(gemini.requests) == 1
    sent = gemini.requests[0]
    assert sent.method == "GET"
    assert sent.url.path == "/v1beta/models"
    assert sent.url.params["pageSize"] == "1"
    assert sent.headers["x-goog-api-key"] == KEY_A  # trimmed before sending

    stored = _stored(db_session, user)
    assert stored is not None
    assert KEY_A not in stored.api_key_encrypted
    assert stored.key_hint == KEY_A[-4:]

    status = client.get("/api/gemini-key", headers=auth)
    assert status.json() == {"connected": True, "key_hint": KEY_A[-4:]}


def test_new_style_aq_key_is_verified_and_stored(
    client: TestClient,
    auth: dict[str, str],
    gemini: FakeGemini,
    db_session: Session,
    user: User,
) -> None:
    resp = _connect(client, auth, f"  {KEY_AQ}\n")

    assert resp.status_code == 200
    assert resp.json() == {
        "connected": True,
        "key_hint": KEY_AQ[-4:],
        "quota_exhausted_notice": False,
    }
    assert len(gemini.requests) == 1
    assert gemini.requests[0].headers["x-goog-api-key"] == KEY_AQ  # trimmed
    assert _stored(db_session, user) is not None


def test_quota_exhausted_verification_stores_with_notice(
    client: TestClient, auth: dict[str, str], gemini: FakeGemini, db_session: Session, user: User
) -> None:
    gemini.handler = lambda _r: _rpc_error(429, "RATE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED")

    resp = _connect(client, auth, KEY_A)

    assert resp.status_code == 200
    assert resp.json()["quota_exhausted_notice"] is True
    assert _stored(db_session, user) is not None


@pytest.mark.parametrize(
    "bad",
    [
        "",  # empty
        "   ",  # whitespace only
        "AIza" + "a" * 10,  # 14 chars, too short (< 20)
        "AIza" + "a" * 35 + "  key with spaces",  # internal whitespace
        "AIza" + "a" * 34 + "\x00",  # control character
    ],
)
def test_malformed_key_is_rejected_offline(
    client: TestClient, auth: dict[str, str], gemini: FakeGemini, db_session: Session, user: User, bad: str
) -> None:
    resp = _connect(client, auth, bad)

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_key_format"
    if bad.strip():
        assert bad not in resp.text
    assert gemini.requests == []
    assert _stored(db_session, user) is None


@pytest.mark.parametrize(
    ("response", "status_code", "code"),
    [
        (lambda: _rpc_error(400, "API_KEY_INVALID", "INVALID_ARGUMENT"), 400, "invalid_key"),
        (lambda: httpx.Response(401), 400, "invalid_key"),
        (lambda: _rpc_error(403, "", "PERMISSION_DENIED"), 400, "invalid_key"),
        (lambda: httpx.Response(500, text="oops"), 503, "could_not_verify"),
        (lambda: httpx.Response(404), 503, "could_not_verify"),
    ],
)
def test_failed_verification_keeps_the_previous_key(
    client: TestClient,
    auth: dict[str, str],
    gemini: FakeGemini,
    db_session: Session,
    user: User,
    response: Callable[[], httpx.Response],
    status_code: int,
    code: str,
) -> None:
    assert _connect(client, auth, KEY_A).status_code == 200
    previous = _stored(db_session, user)
    assert previous is not None
    previous_ciphertext = previous.api_key_encrypted
    gemini.handler = lambda _r: response()

    resp = _connect(client, auth, KEY_B)

    assert resp.status_code == status_code
    assert resp.json()["detail"]["code"] == code
    assert KEY_B not in resp.text
    current = _stored(db_session, user)
    assert current is not None
    assert current.api_key_encrypted == previous_ciphertext
    assert client.get("/api/gemini-key", headers=auth).json()["key_hint"] == KEY_A[-4:]


def test_transport_error_is_could_not_verify(
    client: TestClient, auth: dict[str, str], gemini: FakeGemini, db_session: Session, user: User
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed with {request.headers['x-goog-api-key']}", request=request)

    gemini.handler = boom

    resp = _connect(client, auth, KEY_A)

    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "could_not_verify"
    assert KEY_A not in resp.text
    assert _stored(db_session, user) is None


def test_concurrent_submission_returns_409(
    client: TestClient, auth: dict[str, str], gemini: FakeGemini, user: User
) -> None:
    locks = gemini_key_service._DEFAULT_LOCKS
    assert locks.try_acquire(user.id)
    try:
        resp = _connect(client, auth, KEY_A)
    finally:
        locks.release(user.id)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "key_verification_in_progress"
    assert gemini.requests == []


def test_delete_removes_the_key(
    client: TestClient, auth: dict[str, str], db_session: Session, user: User
) -> None:
    assert _connect(client, auth, KEY_A).status_code == 200

    resp = client.delete("/api/gemini-key", headers=auth)

    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "key_hint": None}
    assert _stored(db_session, user) is None
    assert client.get("/api/gemini-key", headers=auth).json() == {
        "connected": False,
        "key_hint": None,
    }


# ---- key-safe validation handler and access log ---------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"api_key": [KEY_A]},
        {"api_key": {"value": KEY_A}},
        {"api_key": KEY_B, "extra": KEY_A},
    ],
)
def test_validation_errors_never_echo_the_submitted_key(
    client: TestClient, auth: dict[str, str], gemini: FakeGemini, body: dict[str, object]
) -> None:
    resp = client.put("/api/gemini-key", json=body, headers=auth)

    assert resp.status_code == 422
    assert KEY_A not in resp.text
    assert KEY_B not in resp.text
    errors = resp.json()["detail"]
    assert errors, "validation errors are still reported"
    for error in errors:
        assert "input" not in error
        assert "ctx" not in error
        assert {"loc", "msg", "type"} <= error.keys()
    assert gemini.requests == []


def test_access_log_records_only_method_path_and_status(
    client: TestClient, auth: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert _connect(client, auth, KEY_A).status_code == 200
        client.get("/api/gemini-key?probe=1", headers=auth)

    access = [r.getMessage() for r in caplog.records if r.name == "app.access"]
    assert access == ["PUT /api/gemini-key 200", "GET /api/gemini-key 200"]
    assert all(KEY_A not in r.getMessage() for r in caplog.records)

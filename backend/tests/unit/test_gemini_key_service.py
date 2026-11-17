"""Unit tests for GeminiKeyService and ``classify_verification`` (task 8.5).

Gemini is always mocked with ``httpx.MockTransport``; no test opens a real
network connection.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 -- register all ORM models
from app.auth.secrets import KeyFormatError, SecretCipher
from app.db.session import Base
from app.models.gemini_credential import GeminiCredential
from app.models.user import User
from app.services.gemini_errors import VerificationOutcome, classify_verification
from app.services.gemini_key_service import (
    GEMINI_VERIFY_URL,
    GeminiKeyService,
    KeyRejectedError,
    KeyVerificationInProgressError,
    KeyVerificationUnavailableError,
    VerificationLocks,
)
from app.services.sentence_generation.base import MissingGeminiKeyError

KEY_A = "AIza" + "A" * 31 + "aaaa"  # old-style 39-char key
KEY_B = "AIza" + "B" * 31 + "bbbb"  # old-style 39-char key
KEY_AQ = "AQ." + "C" * 36 + "cccc"  # new-style "AQ." key, verified like any other

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


def _rpc(code: int, status: str, reason: str | None = None, *, key: str = KEY_A) -> bytes:
    error: dict = {"code": code, "message": f"API key {key} not valid", "status": status}
    if reason:
        error["details"] = [
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}
        ]
    return json.dumps({"error": error}).encode()


def _models_ok() -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": "models/gemini"}]})


# ---- classify_verification ---------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, b"{}", VerificationOutcome.VALID),
        (400, _rpc(400, "INVALID_ARGUMENT", "API_KEY_INVALID"), VerificationOutcome.INVALID),
        (401, _rpc(401, "UNAUTHENTICATED"), VerificationOutcome.INVALID),
        (403, _rpc(403, "PERMISSION_DENIED"), VerificationOutcome.INVALID),
        (429, _rpc(429, "RESOURCE_EXHAUSTED"), VerificationOutcome.QUOTA_EXHAUSTED),
        (429, None, VerificationOutcome.QUOTA_EXHAUSTED),
        (400, _rpc(400, "RESOURCE_EXHAUSTED"), VerificationOutcome.QUOTA_EXHAUSTED),
        (400, _rpc(400, "INVALID_ARGUMENT"), VerificationOutcome.UNAVAILABLE),
        (404, None, VerificationOutcome.UNAVAILABLE),
        (500, _rpc(500, "INTERNAL"), VerificationOutcome.UNAVAILABLE),
        (503, b"not json", VerificationOutcome.UNAVAILABLE),
        (None, None, VerificationOutcome.UNAVAILABLE),
    ],
)
def test_classify_verification(
    status: int | None, body: bytes | None, expected: VerificationOutcome
) -> None:
    assert classify_verification(status, body) is expected


# ---- service fixtures --------------------------------------------------------


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


@pytest.fixture()
def user(db: Session) -> User:
    learner = User(
        email="learner@example.com",
        name="Learner",
        oidc_issuer="https://accounts.google.com",
        oidc_subject="google-subject",
    )
    db.add(learner)
    db.commit()
    db.refresh(learner)
    return learner


@pytest.fixture()
def cipher() -> SecretCipher:
    return SecretCipher(Fernet.generate_key().decode("ascii"))


class Recorder:
    """MockTransport wrapper recording every request sent to "Gemini"."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self._handler(request)


def _service(
    db: Session,
    cipher: SecretCipher,
    handler: Handler,
    *,
    locks: VerificationLocks | None = None,
    verify_timeout: float = 10.0,
) -> tuple[GeminiKeyService, Recorder]:
    recorder = Recorder(handler)
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    service = GeminiKeyService(
        db,
        cipher=cipher,
        http_client=client,
        locks=locks if locks is not None else VerificationLocks(),
        verify_timeout=verify_timeout,
    )
    return service, recorder


def _respond(response: httpx.Response) -> Handler:
    async def handler(request: httpx.Request) -> httpx.Response:
        return response

    return handler


def _stored(db: Session, user_id: str) -> GeminiCredential | None:
    db.expire_all()
    return db.execute(
        select(GeminiCredential).where(GeminiCredential.user_id == user_id)
    ).scalar_one_or_none()


# ---- connect -----------------------------------------------------------------


def test_connect_verifies_once_then_stores_encrypted_key(
    db: Session, cipher: SecretCipher, user: User
) -> None:
    service, recorder = _service(db, cipher, _respond(_models_ok()))

    result = asyncio.run(service.connect(user.id, f"  {KEY_A}\n"))

    assert result.key_hint == "aaaa"
    assert result.quota_exhausted_notice is False
    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request.method == "GET"
    assert str(request.url).startswith(GEMINI_VERIFY_URL)
    assert request.url.params["pageSize"] == "1"
    assert request.headers["x-goog-api-key"] == KEY_A  # trimmed
    assert KEY_A not in str(request.url)

    row = _stored(db, user.id)
    assert row is not None and row.key_hint == "aaaa"
    assert KEY_A not in row.api_key_encrypted
    assert cipher.decrypt(row.api_key_encrypted) == KEY_A
    assert service.status(user.id).connected is True
    assert service.status(user.id).key_hint == "aaaa"
    assert service.get_api_key(user.id) == KEY_A


def test_connect_accepts_new_style_aq_key(
    db: Session, cipher: SecretCipher, user: User
) -> None:
    service, recorder = _service(db, cipher, _respond(_models_ok()))

    result = asyncio.run(service.connect(user.id, f"  {KEY_AQ}\n"))

    assert result.key_hint == KEY_AQ[-4:]
    assert len(recorder.requests) == 1
    assert recorder.requests[0].headers["x-goog-api-key"] == KEY_AQ  # trimmed
    assert service.get_api_key(user.id) == KEY_AQ


def test_quota_exhausted_key_is_stored_with_notice(
    db: Session, cipher: SecretCipher, user: User
) -> None:
    service, _ = _service(
        db, cipher, _respond(httpx.Response(429, content=_rpc(429, "RESOURCE_EXHAUSTED")))
    )

    result = asyncio.run(service.connect(user.id, KEY_A))

    assert result.quota_exhausted_notice is True
    assert service.get_api_key(user.id) == KEY_A


@pytest.mark.parametrize("raw", ["", "   ", "short", KEY_A[:10] + " " + KEY_A[11:]])
def test_format_failure_stores_nothing_and_sends_no_request(
    db: Session, cipher: SecretCipher, user: User, raw: str
) -> None:
    service, recorder = _service(db, cipher, _respond(_models_ok()))

    with pytest.raises(KeyFormatError):
        asyncio.run(service.connect(user.id, raw))

    assert recorder.requests == []
    assert _stored(db, user.id) is None


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            httpx.Response(400, content=_rpc(400, "INVALID_ARGUMENT", "API_KEY_INVALID", key=KEY_B)),
            KeyRejectedError,
        ),
        (httpx.Response(403, content=_rpc(403, "PERMISSION_DENIED", key=KEY_B)), KeyRejectedError),
        (httpx.Response(500, content=_rpc(500, "INTERNAL", key=KEY_B)), KeyVerificationUnavailableError),
    ],
)
def test_failed_replacement_keeps_previous_key(
    db: Session,
    cipher: SecretCipher,
    user: User,
    response: httpx.Response,
    error: type[Exception],
) -> None:
    ok, _ = _service(db, cipher, _respond(_models_ok()))
    asyncio.run(ok.connect(user.id, KEY_A))
    before = _stored(db, user.id)
    assert before is not None
    ciphertext_before = before.api_key_encrypted

    service, recorder = _service(db, cipher, _respond(response))
    with pytest.raises(error) as exc_info:
        asyncio.run(service.connect(user.id, KEY_B))

    assert len(recorder.requests) == 1
    assert KEY_B not in str(exc_info.value)
    after = _stored(db, user.id)
    assert after is not None
    assert after.api_key_encrypted == ciphertext_before
    assert after.key_hint == "aaaa"
    assert service.get_api_key(user.id) == KEY_A


def test_successful_replacement_overwrites_previous_key(
    db: Session, cipher: SecretCipher, user: User
) -> None:
    service, _ = _service(db, cipher, _respond(_models_ok()))
    asyncio.run(service.connect(user.id, KEY_A))
    asyncio.run(service.connect(user.id, KEY_B))

    rows = db.execute(select(GeminiCredential)).scalars().all()
    assert len(rows) == 1
    assert rows[0].key_hint == "bbbb"
    assert service.get_api_key(user.id) == KEY_B


def test_timeout_maps_to_unavailable_and_stores_nothing(
    db: Session, cipher: SecretCipher, user: User
) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return _models_ok()

    service, recorder = _service(db, cipher, slow, verify_timeout=0.05)

    with pytest.raises(KeyVerificationUnavailableError) as exc_info:
        asyncio.run(service.connect(user.id, KEY_A))

    assert len(recorder.requests) == 1
    assert exc_info.value.__context__ is None
    assert _stored(db, user.id) is None


def test_transport_error_is_not_leaked(
    db: Session, cipher: SecretCipher, user: User, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed with header {KEY_A}", request=request)

    service, _ = _service(db, cipher, broken)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(KeyVerificationUnavailableError) as exc_info:
        asyncio.run(service.connect(user.id, KEY_A))

    err = exc_info.value
    assert err.__context__ is None and err.__cause__ is None
    assert KEY_A not in str(err) and KEY_A not in repr(err)
    assert KEY_A not in caplog.text
    assert _stored(db, user.id) is None


def test_concurrent_submission_is_refused_while_verifying(
    db: Session, cipher: SecretCipher, user: User
) -> None:
    locks = VerificationLocks()

    async def scenario() -> None:
        release = asyncio.Event()

        async def gated(request: httpx.Request) -> httpx.Response:
            await release.wait()
            return _models_ok()

        first, first_recorder = _service(db, cipher, gated, locks=locks)
        second, second_recorder = _service(db, cipher, _respond(_models_ok()), locks=locks)

        task = asyncio.create_task(first.connect(user.id, KEY_A))
        while not first_recorder.requests:
            await asyncio.sleep(0)

        with pytest.raises(KeyVerificationInProgressError):
            await second.connect(user.id, KEY_B)
        assert second_recorder.requests == []

        release.set()
        assert (await task).key_hint == "aaaa"
        assert not locks.is_held(user.id)

        # After completion a new submission is accepted.
        assert (await second.connect(user.id, KEY_B)).key_hint == "bbbb"

    asyncio.run(scenario())


def test_lock_is_released_after_failed_verification(
    db: Session, cipher: SecretCipher, user: User
) -> None:
    locks = VerificationLocks()
    service, _ = _service(
        db, cipher, _respond(httpx.Response(401, content=_rpc(401, "UNAUTHENTICATED"))), locks=locks
    )
    with pytest.raises(KeyRejectedError):
        asyncio.run(service.connect(user.id, KEY_A))
    assert not locks.is_held(user.id)


# ---- status / remove / get_api_key ------------------------------------------


def test_status_and_get_api_key_without_stored_key(
    db: Session, cipher: SecretCipher, user: User
) -> None:
    service, _ = _service(db, cipher, _respond(_models_ok()))
    status = service.status(user.id)
    assert status.connected is False and status.key_hint is None
    with pytest.raises(MissingGeminiKeyError):
        service.get_api_key(user.id)


def test_remove_deletes_key_and_returns_not_connected(
    db: Session, cipher: SecretCipher, user: User
) -> None:
    service, _ = _service(db, cipher, _respond(_models_ok()))
    asyncio.run(service.connect(user.id, KEY_A))

    status = service.remove(user.id)

    assert status.connected is False and status.key_hint is None
    assert _stored(db, user.id) is None
    with pytest.raises(MissingGeminiKeyError):
        service.get_api_key(user.id)
    # Removing again is harmless.
    assert service.remove(user.id).connected is False


def test_undecryptable_stored_key_is_treated_as_missing(
    db: Session, cipher: SecretCipher, user: User
) -> None:
    service, _ = _service(db, cipher, _respond(_models_ok()))
    asyncio.run(service.connect(user.id, KEY_A))

    rotated, _ = _service(db, SecretCipher(Fernet.generate_key()), _respond(_models_ok()))
    with pytest.raises(MissingGeminiKeyError):
        rotated.get_api_key(user.id)

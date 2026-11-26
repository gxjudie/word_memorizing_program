"""Unit tests for the Google AuthService; Google is mocked via httpx.MockTransport."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.auth.oauth import (
    GOOGLE_JWKS_URI,
    GOOGLE_TOKEN_ENDPOINT,
    ConsumedStateRegistry,
    GoogleOAuthClient,
    JWKSCache,
)
from app.auth.session import decode_app_session, is_app_session_revoked
from app.config import Settings
from app.db.session import Base
from app.models.user import User
from app.services.auth_service import (
    AccountVerificationError,
    AuthConfigurationError,
    AuthorizationDeniedError,
    AuthService,
    AuthStateError,
    TokenExchangeError,
)

NOW = datetime(2030, 1, 2, 3, 4, tzinfo=timezone.utc)
SESSION_SECRET = "unit-test-session-secret"
CLIENT_ID = "google-client.apps.googleusercontent.com"
ACCESS_TOKEN = "ya29.google-access-token"
REFRESH_TOKEN = "1//google-refresh-token"


def _b64int(value: int) -> str:
    size = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()


class FakeGoogle:
    """httpx MockTransport handler for Google's token endpoint and JWKS."""

    def __init__(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.private_key.public_key().public_numbers()
        self.jwks = {"keys": [{
            "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "google-key",
            "n": _b64int(numbers.n), "e": _b64int(numbers.e),
        }]}
        self.requests: list[httpx.Request] = []
        self.nonce = ""
        self.claims: dict = {}
        self.token_response: httpx.Response | None = None
        self.jwks_status = 200

    @property
    def token_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url) == GOOGLE_TOKEN_ENDPOINT]

    def id_token(self) -> str:
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": "google-subject-1",
            "email": "learner@example.com",
            "email_verified": True,
            "name": "Learner",
            "nonce": self.nonce,
            "iat": int(NOW.timestamp()),
            "exp": int((NOW + timedelta(hours=1)).timestamp()),
            **self.claims,
        }
        return jwt.encode(claims, self.private_key, algorithm="RS256",
                          headers={"kid": "google-key"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == GOOGLE_TOKEN_ENDPOINT:
            if self.token_response is not None:
                return self.token_response
            return httpx.Response(200, json={
                "access_token": ACCESS_TOKEN,
                "refresh_token": REFRESH_TOKEN,
                "id_token": self.id_token(),
                "expires_in": 3599,
                "scope": "openid email profile",
                "token_type": "Bearer",
            })
        if url == GOOGLE_JWKS_URI:
            if self.jwks_status != 200:
                return httpx.Response(self.jwks_status)
            return httpx.Response(200, json=self.jwks,
                                  headers={"Cache-Control": "public, max-age=3600"})
        return httpx.Response(404)


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
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        db_url="sqlite://",
        google_oauth_client_id=CLIENT_ID,
        google_oauth_client_secret=SecretStr("google-client-secret"),
        oauth_redirect_uri="http://localhost:5173/api/auth/callback",
        session_secret=SESSION_SECRET,
    )


@pytest.fixture()
def service(db, google, settings) -> AuthService:
    oauth_client = GoogleOAuthClient(
        client_id=CLIENT_ID,
        client_secret="google-client-secret",
        redirect_uri=settings.oauth_redirect_uri,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(google)),
        jwks_cache=JWKSCache(),
    )
    return AuthService(
        db,
        settings=settings,
        oauth_client=oauth_client,
        state_registry=ConsumedStateRegistry(),
        clock=lambda: NOW,
    )


def _sign_in(service: AuthService, google: FakeGoogle, *, code="auth-code"):
    start = service.build_authorization_url()
    google.nonce = start.nonce
    return start, asyncio.run(service.handle_callback(
        code=code, state=start.state, error=None, expected=start
    ))


def _all_rows_text(db: Session) -> str:
    chunks = []
    for table in Base.metadata.sorted_tables:
        chunks.extend(repr(tuple(row)) for row in db.execute(table.select()))
    return "\n".join(chunks)


def test_callback_creates_account_by_subject_and_discards_google_tokens(db, google, service):
    start, app_session = _sign_in(service, google)

    user = db.execute(select(User)).scalar_one()
    assert (user.oidc_issuer, user.oidc_subject, user.email, user.name) == (
        "https://accounts.google.com", "google-subject-1", "learner@example.com", "Learner",
    )
    claims = decode_app_session(app_session.token, secret=SESSION_SECRET)
    assert claims.user_id == user.id
    assert claims.session_id == app_session.session_id

    assert len(google.token_requests) == 1
    form = parse_qs(google.token_requests[0].content.decode())
    assert form["code"] == ["auth-code"]
    assert form["code_verifier"] == [start.code_verifier]
    assert form["grant_type"] == ["authorization_code"]

    stored = _all_rows_text(db)
    assert ACCESS_TOKEN not in stored and REFRESH_TOKEN not in stored
    assert ACCESS_TOKEN not in app_session.token


def test_repeat_sign_in_with_other_issuer_spelling_and_new_email_reuses_account(
    db, google, service
):
    _, first = _sign_in(service, google)
    google.claims = {"iss": "accounts.google.com", "email": "renamed@example.com"}
    _, second = _sign_in(service, google)

    assert db.scalar(select(func.count()).select_from(User)) == 1
    user = db.execute(select(User)).scalar_one()
    assert user.email == "renamed@example.com"
    assert user.oidc_issuer == "https://accounts.google.com"
    assert first.user_id == second.user_id == user.id


def test_provider_error_is_denied_without_exchange(google, service):
    start = service.build_authorization_url()
    with pytest.raises(AuthorizationDeniedError):
        asyncio.run(service.handle_callback(
            code=None, state=start.state, error="access_denied", expected=start
        ))
    assert google.requests == []


@pytest.mark.parametrize("case", ["missing", "mismatch", "expired", "no_cookie"])
def test_state_failures_reject_before_any_token_exchange(google, service, case):
    start = service.build_authorization_url()
    state, expected = start.state, start
    if case == "missing":
        state = None
    elif case == "mismatch":
        state = "wrong-state"
    elif case == "expired":
        service._clock = lambda: NOW + timedelta(minutes=10)
    elif case == "no_cookie":
        expected = None

    with pytest.raises(AuthStateError):
        asyncio.run(service.handle_callback(
            code="auth-code", state=state, error=None, expected=expected
        ))
    assert google.requests == []


def test_replayed_state_is_rejected_and_exchange_happens_once(google, service):
    start, _ = _sign_in(service, google)
    with pytest.raises(AuthStateError):
        asyncio.run(service.handle_callback(
            code="auth-code", state=start.state, error=None, expected=start
        ))
    assert len(google.token_requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(500),
        httpx.Response(200, json={"access_token": ACCESS_TOKEN}),
    ],
)
def test_token_exchange_failures_create_no_account(db, google, service, response):
    google.token_response = response
    with pytest.raises(TokenExchangeError) as exc_info:
        _sign_in(service, google)
    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert ACCESS_TOKEN not in str(exc_info.value)
    assert len(google.token_requests) == 1


@pytest.mark.parametrize(
    "claims",
    [{"nonce": "other"}, {"aud": "other-client"}, {"email_verified": False}],
)
def test_id_token_failure_creates_or_modifies_no_account(db, google, service, claims):
    _sign_in(service, google)
    google.claims = {**claims, "email": "attacker@example.com"}
    with pytest.raises(AccountVerificationError):
        _sign_in(service, google)
    user = db.execute(select(User)).scalar_one()
    assert user.email == "learner@example.com"


def test_jwks_failure_is_account_verification_error(db, google, service):
    google.jwks_status = 503
    with pytest.raises(AccountVerificationError):
        _sign_in(service, google)
    assert db.scalar(select(func.count()).select_from(User)) == 0


def test_logout_revokes_the_session_id_until_expiry(db, google, service):
    _, app_session = _sign_in(service, google)
    service.logout(app_session.session_id, app_session.expires_at)
    assert is_app_session_revoked(db, app_session.session_id) is True


def test_missing_client_configuration_is_a_configuration_error(db, settings):
    unconfigured = AuthService(
        db, settings=settings.model_copy(update={"google_oauth_client_id": ""})
    )
    with pytest.raises(AuthConfigurationError):
        unconfigured.build_authorization_url()

"""Integration tests for the Google sign-in auth routes (task 9.1, Req 9).

The real ``AuthService``, ``GoogleOAuthClient``, App_Session, and
``GeminiKeyService`` run against an in-memory SQLite database. Google is
always mocked: the token endpoint and JWKS are served by an
``httpx.MockTransport`` handler that signs ID tokens with a locally generated
RSA key. No test reaches the network.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient
from jose import jwt
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.api.deps import get_auth_service, get_gemini_http_client
from app.auth.oauth import (
    AUTH_START_COOKIE,
    GOOGLE_AUTHORIZATION_ENDPOINT,
    GOOGLE_JWKS_URI,
    GOOGLE_TOKEN_ENDPOINT,
    AuthStart,
    ConsumedStateRegistry,
    GoogleOAuthClient,
    JWKSCache,
    encode_auth_start_cookie,
    pkce_challenge,
)
from app.auth.session import decode_app_session
from app.config import Settings, get_settings
from app.db.session import Base, get_db
from app.main import create_app
from app.models.gemini_credential import GeminiCredential
from app.models.user import User
from app.services.auth_service import AuthService

CLIENT_ID = "route-test.apps.googleusercontent.com"
CLIENT_SECRET = "route-test-google-client-secret"
SESSION_SECRET = "route-test-session-secret"
ACCESS_TOKEN = "ya29.route-test-google-access-token"
REFRESH_TOKEN = "1//route-test-google-refresh-token"
NAVIGATION = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}


def _b64int(value: int) -> str:
    size = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()


class FakeGoogle:
    """MockTransport handler for Google's token endpoint and JWKS."""

    def __init__(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.private_key.public_key().public_numbers()
        self.jwks = {"keys": [{
            "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "route-key",
            "n": _b64int(numbers.n), "e": _b64int(numbers.e),
        }]}
        self.requests: list[httpx.Request] = []
        self.claims: dict = {}
        # The nonce sent in the authorization URL; tests copy it here.
        self.current_nonce = ""
        self.token_outcome = "ok"  # ok | http_error | no_id_token | transport

    @property
    def token_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url) == GOOGLE_TOKEN_ENDPOINT]

    def _id_token(self, nonce: str) -> str:
        now = datetime.now(timezone.utc)
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": "google-subject-1",
            "email": "learner@example.com",
            "email_verified": True,
            "name": "Learner",
            "nonce": nonce,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            **self.claims,
        }
        return jwt.encode(
            claims, self.private_key, algorithm="RS256", headers={"kid": "route-key"}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == GOOGLE_TOKEN_ENDPOINT:
            if self.token_outcome == "transport":
                raise httpx.ConnectError("unreachable", request=request)
            if self.token_outcome == "http_error":
                return httpx.Response(400, json={"error": "invalid_grant"})
            body = {
                "access_token": ACCESS_TOKEN,
                "refresh_token": REFRESH_TOKEN,
                "expires_in": 3599,
                "token_type": "Bearer",
                "scope": "openid email profile",
            }
            if self.token_outcome != "no_id_token":
                body["id_token"] = self._id_token(self.current_nonce)
            return httpx.Response(200, json=body)
        if url == GOOGLE_JWKS_URI:
            return httpx.Response(
                200, json=self.jwks, headers={"Cache-Control": "public, max-age=3600"}
            )
        return httpx.Response(404)


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
        google_oauth_client_id=CLIENT_ID,
        google_oauth_client_secret=SecretStr(CLIENT_SECRET),
        oauth_redirect_uri="http://testserver/api/auth/callback",
        session_secret=SESSION_SECRET,
        token_encryption_key=Fernet.generate_key().decode("ascii"),
    )


@pytest.fixture()
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture()
def client(
    db_session: Session, settings: Settings, google: FakeGoogle
) -> Iterator[TestClient]:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(google))
    oauth_client = GoogleOAuthClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=settings.oauth_redirect_uri,
        http_client=http_client,
        jwks_cache=JWKSCache(),
    )
    registry = ConsumedStateRegistry()

    def _get_db() -> Iterator[Session]:
        yield db_session

    def _auth_service(
        db: Session = Depends(get_db), s: Settings = Depends(get_settings)
    ) -> AuthService:
        return AuthService(db, settings=s, oauth_client=oauth_client, state_registry=registry)

    app = create_app()
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_auth_service] = _auth_service
    app.dependency_overrides[get_gemini_http_client] = lambda: http_client
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _user_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(User)).scalar_one()


def _start_login(client: TestClient, google: FakeGoogle) -> dict[str, str]:
    response = client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 302
    query = {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}
    google.current_nonce = query["nonce"]
    return query


def _sign_in(client: TestClient, google: FakeGoogle) -> str:
    query = _start_login(client, google)
    response = client.get(
        "/api/auth/callback", params={"code": "google-code", "state": query["state"]}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


# ---- GET /api/auth/login (Req 9.1, 9.2, 9.11) ----


def test_login_redirects_to_google_with_fresh_pkce_state_nonce_and_cookie(
    client: TestClient, google: FakeGoogle, settings: Settings
):
    response = client.get("/api/auth/login", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(GOOGLE_AUTHORIZATION_ENDPOINT + "?")
    query = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == settings.oauth_redirect_uri
    assert query["response_type"] == "code"
    assert query["scope"] == "openid email profile"
    assert query["code_challenge_method"] == "S256"
    assert query["prompt"] == "select_account"
    assert query["state"] and query["nonce"] and query["code_challenge"]
    assert "access_type" not in query

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{AUTH_START_COOKIE}=")
    lowered = set_cookie.lower()
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "path=/api/auth" in lowered
    assert "max-age=600" in lowered
    assert "secure" not in lowered  # redirect URI is http in this test
    cookie = client.cookies.get(AUTH_START_COOKIE)
    assert cookie and query["state"] not in cookie and query["nonce"] not in cookie

    second = _start_login(client, google)
    assert second["state"] != query["state"]
    assert second["nonce"] != query["nonce"]
    assert second["code_challenge"] != query["code_challenge"]
    assert google.requests == []  # login never talks to Google's backend


@pytest.mark.parametrize(
    "overrides, missing_name",
    [
        ({"google_oauth_client_id": ""}, "GOOGLE_OAUTH_CLIENT_ID"),
        ({"google_oauth_client_secret": SecretStr("")}, "GOOGLE_OAUTH_CLIENT_SECRET"),
        ({"token_encryption_key": ""}, "TOKEN_ENCRYPTION_KEY"),
    ],
)
def test_login_without_configuration_shows_error_page_without_redirect(
    client: TestClient,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    overrides: dict,
    missing_name: str,
):
    for name, value in overrides.items():
        setattr(settings, name, value)
    caplog.set_level(logging.DEBUG)

    page = client.get("/api/auth/login", headers=NAVIGATION, follow_redirects=False)
    api = client.get("/api/auth/login", follow_redirects=False)

    assert page.status_code == 503
    assert "location" not in page.headers
    assert page.headers["content-type"].startswith("text/html")
    assert api.status_code == 503
    assert "location" not in api.headers
    assert api.json()["code"] == "sign_in_unavailable"
    assert AUTH_START_COOKIE not in client.cookies
    assert missing_name in caplog.text
    assert CLIENT_SECRET not in caplog.text
    assert settings.token_encryption_key == "" or settings.token_encryption_key not in caplog.text


# ---- GET /api/auth/callback success (Req 9.6, 9.8, 9.9, 9.12) ----


def test_browser_callback_creates_account_and_bridges_only_the_app_session(
    client: TestClient,
    google: FakeGoogle,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    query = _start_login(client, google)
    verifier_cookie = client.cookies.get(AUTH_START_COOKIE)
    assert verifier_cookie

    response = client.get(
        "/api/auth/callback",
        params={"code": "google-code", "state": query["state"]},
        headers=NAVIGATION,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "localStorage.setItem('jvt.token', token)" in response.text
    assert "window.location.replace('/' + result)" in response.text
    assert AUTH_START_COOKIE not in client.cookies

    # Exactly one exchange with the PKCE verifier matching the sent challenge.
    assert len(google.token_requests) == 1
    form = parse_qs(google.token_requests[0].content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["google-code"]
    assert form["client_secret"] == [CLIENT_SECRET]
    assert pkce_challenge(form["code_verifier"][0]) == query["code_challenge"]

    user = db_session.execute(select(User)).scalar_one()
    assert (user.oidc_issuer, user.oidc_subject) == ("https://accounts.google.com", "google-subject-1")
    assert user.email == "learner@example.com"

    # The bridge carries the first-party session for that account.
    token = response.text.split("const token = ")[1].split(";")[0].strip('"')
    assert decode_app_session(token, secret=SESSION_SECRET).user_id == user.id

    # Req 9.12: Google tokens appear in no header, body, or log. (The test
    # client itself logs the callback URL, so the code is checked in the
    # response only.)
    exposed = response.text + str(response.headers) + caplog.text
    for secret in (ACCESS_TOKEN, REFRESH_TOKEN):
        assert secret not in exposed
    assert "google-code" not in response.text + str(response.headers)


def test_api_callback_returns_bearer_session_and_resolves_same_account_by_subject(
    client: TestClient, google: FakeGoogle, db_session: Session
):
    first = _sign_in(client, google)
    google.claims = {"email": "renamed@example.com", "iss": "accounts.google.com"}
    second = _sign_in(client, google)

    assert _user_count(db_session) == 1
    user = db_session.execute(select(User)).scalar_one()
    db_session.refresh(user)
    assert user.email == "renamed@example.com"
    assert user.oidc_issuer == "https://accounts.google.com"
    for token in (first, second):
        assert decode_app_session(token, secret=SESSION_SECRET).user_id == user.id


# ---- GET /api/auth/callback failures (Req 9.4, 9.5, 9.7, 9.10) ----


def test_state_mismatch_is_rejected_before_any_token_exchange(
    client: TestClient, google: FakeGoogle, db_session: Session
):
    _start_login(client, google)

    response = client.get(
        "/api/auth/callback", params={"code": "google-code", "state": "wrong-state"}
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "login_not_verified",
        "message": "Login could not be verified",
        "retry_url": "/api/auth/login",
    }
    assert google.requests == []
    assert _user_count(db_session) == 0
    assert AUTH_START_COOKIE not in client.cookies


def test_missing_state_or_cookie_is_rejected_before_any_token_exchange(
    client: TestClient, google: FakeGoogle
):
    no_cookie = client.get("/api/auth/callback", params={"code": "c", "state": "s"})
    _start_login(client, google)
    no_state = client.get("/api/auth/callback", params={"code": "c"})

    assert no_cookie.json()["code"] == "login_not_verified"
    assert no_state.json()["code"] == "login_not_verified"
    assert google.requests == []


def test_expired_state_is_rejected_before_any_token_exchange(
    client: TestClient, google: FakeGoogle, settings: Settings
):
    expired = AuthStart(
        state="expired-state",
        nonce="nonce",
        code_verifier="v" * 64,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    cookie = encode_auth_start_cookie(
        expired, Fernet(settings.token_encryption_key.encode("ascii"))
    )
    client.cookies.set(AUTH_START_COOKIE, cookie, path="/api/auth")

    response = client.get(
        "/api/auth/callback", params={"code": "c", "state": "expired-state"}
    )

    assert response.json()["code"] == "login_not_verified"
    assert google.requests == []


def test_replayed_state_is_rejected_without_a_second_exchange(
    client: TestClient, google: FakeGoogle
):
    query = _start_login(client, google)
    cookie = client.cookies.get(AUTH_START_COOKIE)
    first = client.get(
        "/api/auth/callback", params={"code": "google-code", "state": query["state"]}
    )
    assert first.status_code == 200

    client.cookies.set(AUTH_START_COOKIE, cookie, path="/api/auth")
    replay = client.get(
        "/api/auth/callback", params={"code": "google-code", "state": query["state"]}
    )

    assert replay.json()["code"] == "login_not_verified"
    assert len(google.token_requests) == 1


def test_authorization_denied_creates_no_session_and_offers_retry(
    client: TestClient, google: FakeGoogle, db_session: Session
):
    query = _start_login(client, google)

    response = client.get(
        "/api/auth/callback", params={"error": "access_denied", "state": query["state"]}
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": "sign_in_not_completed",
        "message": "Sign-in was not completed",
        "retry_url": "/api/auth/login",
    }
    assert google.requests == []
    assert _user_count(db_session) == 0


@pytest.mark.parametrize("outcome", ["http_error", "no_id_token", "transport"])
def test_token_exchange_failure_creates_no_session(
    client: TestClient, google: FakeGoogle, db_session: Session, outcome: str
):
    google.token_outcome = outcome
    query = _start_login(client, google)

    response = client.get(
        "/api/auth/callback", params={"code": "google-code", "state": query["state"]}
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "sign_in_service_unreachable",
        "message": "Sign-in could not be completed",
        "retry_url": "/api/auth/login",
    }
    assert "access_token" not in response.text
    assert ACCESS_TOKEN not in response.text
    assert len(google.token_requests) == 1
    assert _user_count(db_session) == 0


@pytest.mark.parametrize(
    "claims",
    [
        {"email_verified": False},
        {"nonce": "some-other-nonce"},
        {"aud": "another-client.apps.googleusercontent.com"},
        {"iss": "https://evil.example.com"},
    ],
)
def test_invalid_id_token_creates_no_account_and_no_session(
    client: TestClient, google: FakeGoogle, db_session: Session, claims: dict
):
    google.claims = claims
    query = _start_login(client, google)

    response = client.get(
        "/api/auth/callback", params={"code": "google-code", "state": query["state"]}
    )

    assert response.status_code == 401
    assert response.json() == {
        "code": "account_not_verified",
        "message": "Account could not be verified",
        "retry_url": "/api/auth/login",
    }
    assert "access_token" not in response.text
    assert _user_count(db_session) == 0


def test_browser_callback_error_bridges_safe_code_and_clears_session(
    client: TestClient, google: FakeGoogle
):
    query = _start_login(client, google)

    response = client.get(
        "/api/auth/callback",
        params={"error": "access_denied", "state": query["state"]},
        headers=NAVIGATION,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'const error = "sign_in_not_completed"' in response.text
    assert "const token = null" in response.text
    assert "localStorage.removeItem('jvt.token')" in response.text
    assert "access_denied" not in response.text
    assert AUTH_START_COOKIE not in client.cookies


# ---- POST /api/auth/logout and GET /api/auth/me (Req 9.13, 9.14, 10.12) ----


def test_me_returns_identity_and_key_status_without_the_key(
    client: TestClient, google: FakeGoogle, db_session: Session
):
    token = _sign_in(client, google)
    headers = {"Authorization": f"Bearer {token}"}

    before = client.get("/api/auth/me", headers=headers)
    assert before.status_code == 200
    assert before.json() == {
        "email": "learner@example.com",
        "name": "Learner",
        "gemini_key": {"connected": False, "key_hint": None},
    }

    user = db_session.execute(select(User)).scalar_one()
    db_session.add(GeminiCredential(
        user_id=user.id, api_key_encrypted="ciphertext-not-a-key", key_hint="abcd"
    ))
    db_session.commit()

    after = client.get("/api/auth/me", headers=headers)
    assert after.json()["gemini_key"] == {"connected": True, "key_hint": "abcd"}
    assert "ciphertext-not-a-key" not in after.text


def test_me_and_logout_require_a_session(client: TestClient):
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout").status_code == 401
    bad = {"Authorization": "Bearer not-a-session"}
    assert client.get("/api/auth/me", headers=bad).status_code == 401
    assert client.post("/api/auth/logout", headers=bad).status_code == 401


def test_logout_revokes_the_session_for_every_later_request(
    client: TestClient, google: FakeGoogle
):
    token = _sign_in(client, google)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    response = client.post("/api/auth/logout", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"success": True}
    revoked_me = client.get("/api/auth/me", headers=headers)
    assert revoked_me.status_code == 401
    assert revoked_me.json() == {"detail": "Not authenticated"}
    assert client.get("/api/words", headers=headers).status_code == 401
    assert client.post("/api/auth/logout", headers=headers).status_code == 401

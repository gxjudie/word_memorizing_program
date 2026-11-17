"""Unit tests for the Google OAuth primitives (Google always mocked)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet

from app.auth.oauth import (
    AUTHORIZATION_TTL,
    GOOGLE_AUTHORIZATION_ENDPOINT,
    GOOGLE_JWKS_URI,
    GOOGLE_TOKEN_ENDPOINT,
    ConsumedStateRegistry,
    GoogleOAuthClient,
    JWKSCache,
    OAuthProtocolError,
    OAuthTransportError,
    create_authorization_start,
    decode_auth_start_cookie,
    encode_auth_start_cookie,
)

NOW = datetime(2030, 1, 2, 3, 4, tzinfo=timezone.utc)
REDIRECT = "http://localhost:5173/api/auth/callback"


def _start(**kwargs):
    return create_authorization_start(
        client_id="google-client", redirect_uri=REDIRECT, now=NOW, **kwargs
    )


def test_authorization_url_has_google_params_pkce_and_only_identity_scopes():
    start = _start()
    parsed = urlparse(start.authorization_url)
    query = parse_qs(parsed.query)
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(start.code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == GOOGLE_AUTHORIZATION_ENDPOINT
    assert query == {
        "client_id": ["google-client"],
        "redirect_uri": [REDIRECT],
        "response_type": ["code"],
        "scope": ["openid email profile"],
        "state": [start.state],
        "nonce": [start.nonce],
        "code_challenge": [expected_challenge],
        "code_challenge_method": ["S256"],
        "prompt": ["select_account"],
    }
    assert "access_type" not in query
    assert 43 <= len(start.code_verifier) <= 128
    assert len(start.state) == 43  # 32 random bytes, base64url
    assert start.expires_at == NOW + AUTHORIZATION_TTL


def test_each_login_attempt_gets_fresh_state_nonce_and_verifier():
    first, second = _start(), _start()
    assert first.state != second.state
    assert first.nonce != second.nonce
    assert first.code_verifier != second.code_verifier


def test_auth_start_cookie_round_trips_encrypted_and_rejects_tampering():
    cipher = Fernet(Fernet.generate_key())
    start = _start()
    encoded = encode_auth_start_cookie(start, cipher)

    assert start.state not in encoded
    assert start.code_verifier not in encoded
    decoded = decode_auth_start_cookie(encoded, cipher)
    assert decoded is not None
    assert (decoded.state, decoded.nonce, decoded.code_verifier, decoded.expires_at) == (
        start.state, start.nonce, start.code_verifier, start.expires_at,
    )
    assert decode_auth_start_cookie(encoded[:-2] + "xx", cipher) is None
    assert decode_auth_start_cookie(encoded, Fernet(Fernet.generate_key())) is None
    assert decode_auth_start_cookie(None, cipher) is None


def test_consumed_state_registry_rejects_replay_until_expiry():
    registry = ConsumedStateRegistry()
    expires = NOW + timedelta(minutes=10)
    assert registry.consume("state-1", expires_at=expires, now=NOW) is True
    assert registry.consume("state-1", expires_at=expires, now=NOW + timedelta(minutes=9)) is False
    assert registry.consume("state-2", expires_at=expires, now=NOW) is True
    # After expiry the entry is purged (the callback rejects expired state anyway).
    assert registry.is_consumed("state-1", now=expires) is False


def _client(handler, **kwargs):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoogleOAuthClient(
        client_id="google-client",
        client_secret="google-secret",
        redirect_uri=REDIRECT,
        http_client=http,
        jwks_cache=kwargs.pop("jwks_cache", JWKSCache()),
        **kwargs,
    )


def test_code_exchange_posts_pkce_verifier_and_client_secret_once():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id_token": "id.token.value", "access_token": "a"})

    payload = asyncio.run(_client(handler).exchange_code(code="the-code", code_verifier="v" * 50))

    assert payload["id_token"] == "id.token.value"
    assert len(calls) == 1
    assert str(calls[0].url) == GOOGLE_TOKEN_ENDPOINT
    form = parse_qs(calls[0].content.decode())
    assert form == {
        "grant_type": ["authorization_code"],
        "code": ["the-code"],
        "code_verifier": ["v" * 50],
        "redirect_uri": [REDIRECT],
        "client_id": ["google-client"],
        "client_secret": ["google-secret"],
    }


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(400, json={"error": "invalid_grant"}), OAuthProtocolError),
        (httpx.Response(200, json={"access_token": "only-access"}), OAuthProtocolError),
        (httpx.Response(200, text="not json"), OAuthProtocolError),
    ],
)
def test_code_exchange_rejects_error_or_missing_id_token(response, error):
    with pytest.raises(error):
        asyncio.run(_client(lambda _: response).exchange_code(code="c", code_verifier="v" * 50))


def test_code_exchange_transport_error_and_timeout_are_transport_errors():
    def broken(request):
        raise httpx.ConnectError("boom google-secret", request=request)

    with pytest.raises(OAuthTransportError) as exc_info:
        asyncio.run(_client(broken).exchange_code(code="c", code_verifier="v" * 50))
    assert "google-secret" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None

    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"id_token": "x"})

    with pytest.raises(OAuthTransportError):
        asyncio.run(
            _client(slow, token_timeout=0.05).exchange_code(code="c", code_verifier="v" * 50)
        )


def test_jwks_is_cached_per_max_age_and_refetched_once_for_unknown_kid():
    clock = [0.0]
    served = [{"keys": [{"kid": "k1", "kty": "RSA"}]}]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200, json=served[0], headers={"Cache-Control": "public, max-age=100"}
        )

    client = _client(handler, jwks_cache=JWKSCache(monotonic=lambda: clock[0]))

    asyncio.run(client.get_jwks(kid="k1"))
    asyncio.run(client.get_jwks(kid="k1"))
    assert calls == [GOOGLE_JWKS_URI]

    served[0] = {"keys": [{"kid": "k2", "kty": "RSA"}]}
    jwks = asyncio.run(client.get_jwks(kid="k2"))  # unknown kid -> one refetch
    assert len(calls) == 2
    assert jwks["keys"][0]["kid"] == "k2"

    clock[0] = 101.0  # cache expired
    asyncio.run(client.get_jwks(kid="k2"))
    assert len(calls) == 3

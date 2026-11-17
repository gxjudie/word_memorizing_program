"""Unit tests for the pure Google ID-token validator (locally signed tokens)."""
from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.auth.oidc import IDTokenValidationError, validate_google_id_token

NOW = datetime(2030, 1, 2, 3, 4, tzinfo=timezone.utc)
CLIENT_ID = "google-client.apps.googleusercontent.com"
NONCE = "nonce-1"


def _b64int(value: int) -> str:
    size = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()


def _jwk(private_key, kid: str) -> dict:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
        "n": _b64int(numbers.n), "e": _b64int(numbers.e),
    }


@pytest.fixture(scope="module")
def keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, {"keys": [_jwk(private_key, "google-key")]}


def _token(private_key, *, kid="google-key", algorithm="RS256", drop=(), **overrides):
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "learner@example.com",
        "email_verified": True,
        "name": "Learner",
        "nonce": NONCE,
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 3600,
    }
    claims.update(overrides)
    for name in drop:
        claims.pop(name)
    return jwt.encode(claims, private_key, algorithm=algorithm, headers={"kid": kid})


def _validate(token, jwks, *, nonce=NONCE):
    return validate_google_id_token(
        token, jwks, client_id=CLIENT_ID, expected_nonce=nonce, now=NOW
    )


@pytest.mark.parametrize("issuer", ["https://accounts.google.com", "accounts.google.com"])
def test_valid_token_returns_identity_with_normalized_issuer(keys, issuer):
    private_key, jwks = keys
    identity = _validate(_token(private_key, iss=issuer), jwks)
    assert identity.issuer == "https://accounts.google.com"
    assert identity.subject == "1234567890"
    assert identity.email == "learner@example.com"
    assert identity.name == "Learner"


def test_expiry_leeway_boundary_is_300_seconds(keys):
    private_key, jwks = keys
    now_ts = int(NOW.timestamp())
    assert _validate(_token(private_key, exp=now_ts - 299), jwks).subject == "1234567890"
    with pytest.raises(IDTokenValidationError):
        _validate(_token(private_key, exp=now_ts - 300), jwks)


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example.com"},
        {"aud": "another-client"},
        {"exp": int(NOW.timestamp()) - 3600},
        {"nonce": "other-nonce"},
        {"email_verified": False},
        {"email_verified": "true"},
    ],
)
def test_each_claim_defect_is_rejected(keys, overrides):
    private_key, jwks = keys
    with pytest.raises(IDTokenValidationError):
        _validate(_token(private_key, **overrides), jwks)


@pytest.mark.parametrize("claim", ["email_verified", "nonce", "exp"])
def test_missing_required_claim_is_rejected(keys, claim):
    private_key, jwks = keys
    with pytest.raises(IDTokenValidationError):
        _validate(_token(private_key, drop=(claim,)), jwks)


def test_wrong_signing_key_unknown_kid_and_non_rs256_are_rejected(keys):
    private_key, jwks = keys
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(IDTokenValidationError):
        _validate(_token(attacker), jwks)  # same kid, wrong key
    with pytest.raises(IDTokenValidationError):
        _validate(_token(private_key, kid="unknown"), jwks)
    with pytest.raises(IDTokenValidationError):
        _validate(_token(private_key, algorithm="RS512"), jwks)
    hs_token = jwt.encode(
        {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "1",
         "email": "a@b.c", "email_verified": True, "nonce": NONCE,
         "exp": int(NOW.timestamp()) + 60},
        "shared-secret", algorithm="HS256", headers={"kid": "google-key"},
    )
    with pytest.raises(IDTokenValidationError):
        _validate(hs_token, jwks)


def test_malformed_token_is_rejected(keys):
    _, jwks = keys
    with pytest.raises(IDTokenValidationError):
        _validate("not-a-jwt", jwks)

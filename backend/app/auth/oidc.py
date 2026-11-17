"""Pure Google ID-token validation (Req 9.8, 9.10).

``validate_google_id_token`` is a pure function over the token, a JWKS
mapping, the expected client ID and nonce, and an injected clock. It performs
no I/O, which makes it a fast Hypothesis target (Property 25).
"""
from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from jose import jwk, jws
from jose.exceptions import JOSEError

from app.auth.oauth import GOOGLE_ISSUER, GOOGLE_ISSUERS

__all__ = [
    "ID_TOKEN_ALGORITHM",
    "DEFAULT_LEEWAY_SECONDS",
    "IDTokenValidationError",
    "GoogleIdentity",
    "validate_google_id_token",
    "unverified_key_id",
]

ID_TOKEN_ALGORITHM = "RS256"
DEFAULT_LEEWAY_SECONDS = 300


class IDTokenValidationError(Exception):
    """The Google ID token cannot establish a trusted identity."""


@dataclass(frozen=True)
class GoogleIdentity:
    """Claims that are safe to use after all six checks passed.

    ``issuer`` is always normalized to ``https://accounts.google.com`` so both
    Google issuer spellings resolve to the same account (Req 9.9).
    """

    issuer: str
    subject: str
    email: str
    name: str | None


def _fail(message: str) -> IDTokenValidationError:
    return IDTokenValidationError(message)


def unverified_key_id(id_token: str) -> str | None:
    """Return the ``kid`` from the token header without trusting it."""

    try:
        header = jws.get_unverified_header(id_token)
    except JOSEError:
        return None
    kid = header.get("kid")
    return kid if isinstance(kid, str) else None


def _select_key(jwks: Mapping[str, Any], kid: str) -> Mapping[str, Any]:
    keys = jwks.get("keys") if isinstance(jwks, Mapping) else None
    if not isinstance(keys, list):
        raise _fail("Signing keys are unavailable")
    for key in keys:
        if isinstance(key, Mapping) and key.get("kid") == kid:
            if key.get("kty") != "RSA":
                raise _fail("Signing key type is not RSA")
            if key.get("alg") not in (None, ID_TOKEN_ALGORITHM):
                raise _fail("Signing key algorithm is not RS256")
            return key
    raise _fail("No signing key matches the token")


def _verified_claims(id_token: str, jwks: Mapping[str, Any]) -> dict[str, Any]:
    """Check (4): RS256 signature against the JWKS key matching ``kid``."""

    try:
        header = jws.get_unverified_header(id_token)
    except JOSEError:
        raise _fail("ID token is malformed") from None
    if header.get("alg") != ID_TOKEN_ALGORITHM:
        raise _fail("ID token algorithm is not RS256")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise _fail("ID token has no key ID")
    key_data = _select_key(jwks, kid)
    try:
        key = jwk.construct(dict(key_data), ID_TOKEN_ALGORITHM)
        payload = jws.verify(id_token, key, algorithms=[ID_TOKEN_ALGORITHM])
    except (JOSEError, ValueError, TypeError):
        raise _fail("ID token signature is invalid") from None
    try:
        claims = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        raise _fail("ID token claims are malformed") from None
    if not isinstance(claims, dict):
        raise _fail("ID token claims are malformed")
    return claims


def validate_google_id_token(
    id_token: str,
    jwks: Mapping[str, Any],
    *,
    client_id: str,
    expected_nonce: str,
    now: datetime,
    leeway_seconds: int = DEFAULT_LEEWAY_SECONDS,
) -> GoogleIdentity:
    """Accept the token only if all six checks pass, else raise.

    1. ``iss`` is ``https://accounts.google.com`` or ``accounts.google.com``.
    2. ``aud`` equals ``client_id``.
    3. ``exp`` is later than ``now - leeway_seconds``.
    4. The RS256 signature verifies with the JWKS key whose ``kid`` matches.
    5. ``nonce`` equals ``expected_nonce`` (constant-time comparison).
    6. ``email_verified`` is ``true``.
    """

    if not isinstance(id_token, str) or not id_token:
        raise _fail("ID token is missing")
    claims = _verified_claims(id_token, jwks)

    issuer = claims.get("iss")
    if not isinstance(issuer, str) or issuer not in GOOGLE_ISSUERS:
        raise _fail("ID token issuer is not Google")

    audience = claims.get("aud")
    if not client_id or not (
        audience == client_id
        or (isinstance(audience, list) and audience == [client_id])
    ):
        raise _fail("ID token audience does not match")

    expiry = claims.get("exp")
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
        raise _fail("ID token expiry is missing")
    now_ts = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).timestamp()
    if not expiry > now_ts - leeway_seconds:
        raise _fail("ID token has expired")

    nonce = claims.get("nonce")
    if (
        not isinstance(nonce, str)
        or not expected_nonce
        or not hmac.compare_digest(nonce.encode("utf-8"), expected_nonce.encode("utf-8"))
    ):
        raise _fail("ID token nonce does not match")

    if claims.get("email_verified") is not True:
        raise _fail("ID token email is not verified")

    subject = claims.get("sub")
    email = claims.get("email")
    if not isinstance(subject, str) or not subject:
        raise _fail("ID token has no subject")
    if not isinstance(email, str) or not email:
        raise _fail("ID token has no email")
    name = claims.get("name")
    if not isinstance(name, str) or not name:
        name = None

    return GoogleIdentity(
        issuer=GOOGLE_ISSUER,
        subject=subject,
        email=email,
        name=name,
    )

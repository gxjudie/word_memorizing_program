"""Password hashing and JWT primitives for backend-owned identity.

This module implements the low-level security helpers the Auth Service builds
on (see the "opencode-style" auth decision in the design): the Learner
authenticates to *our* application, so the backend hashes passwords and issues
its own stateless session tokens.

Two concerns live here, deliberately kept free of any web-framework or ORM
imports so they are trivially unit-testable:

* **Password hashing** — argon2 via ``passlib`` (``hash_password`` /
  ``verify_password``). Argon2 salts automatically, so hashing the same
  password twice yields different digests, and ``verify_password`` round-trips
  either one.
* **JWT sessions** — ``issue_token`` mints a signed, expiring token for a user
  id; ``verify_token`` validates the signature and expiry and returns the
  subject (user id). Both sign/verify with ``Settings.jwt_secret``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

__all__ = [
    "hash_password",
    "verify_password",
    "issue_token",
    "verify_token",
    "InvalidTokenError",
    "JWT_ALGORITHM",
    "DEFAULT_TOKEN_TTL",
]

JWT_ALGORITHM: str = "HS256"
"""Symmetric signing algorithm for session tokens (HMAC-SHA256)."""

DEFAULT_TOKEN_TTL: timedelta = timedelta(hours=12)
"""Default session lifetime applied by ``issue_token`` when none is given."""

# Argon2 password hashing context. ``passlib`` manages salting and the encoded
# parameters, so verification needs only the stored hash and the candidate.
_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


class InvalidTokenError(Exception):
    """Raised when a JWT is malformed, tampered with, or expired."""


def hash_password(password: str) -> str:
    """Hash a plaintext password with argon2.

    Args:
        password: The plaintext password to hash.

    Returns:
        The argon2 hash string (salt and parameters encoded within it).
    """
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored argon2 hash.

    Args:
        password: The candidate plaintext password.
        password_hash: A hash previously produced by :func:`hash_password`.

    Returns:
        ``True`` if the password matches the hash, ``False`` otherwise. A
        malformed or unrecognizable hash is treated as a non-match rather than
        an error, so verification is safe to call on untrusted stored values.
    """
    try:
        return _pwd_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def issue_token(
    user_id: str,
    *,
    expires_in: timedelta | None = None,
    now: datetime | None = None,
) -> str:
    """Issue a signed JWT session token for a user.

    The token's ``sub`` claim is the user id; ``iat`` and ``exp`` bound its
    validity. It is signed with ``Settings.jwt_secret`` using HS256.

    Args:
        user_id: The authenticated Learner's id, placed in the ``sub`` claim.
        expires_in: Token lifetime; defaults to :data:`DEFAULT_TOKEN_TTL`.
        now: Issue time; defaults to the current UTC time. Injectable for tests.

    Returns:
        The encoded, signed JWT as a compact string.
    """
    issued_at = now or datetime.now(timezone.utc)
    ttl = expires_in if expires_in is not None else DEFAULT_TOKEN_TTL
    claims = {
        "sub": user_id,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + ttl).timestamp()),
    }
    return jwt.encode(claims, get_settings().jwt_secret, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> str:
    """Verify a session token's signature and expiry and return its subject.

    Args:
        token: The encoded JWT from a client (e.g. a Bearer token).

    Returns:
        The ``sub`` claim: the authenticated user's id.

    Raises:
        InvalidTokenError: If the token is malformed, has a bad signature, is
            expired, or lacks a ``sub`` claim.
    """
    try:
        payload = jwt.decode(
            token, get_settings().jwt_secret, algorithms=[JWT_ALGORITHM]
        )
    except JWTError as exc:
        raise InvalidTokenError("Invalid or expired session token") from exc

    subject = payload.get("sub")
    if not subject:
        raise InvalidTokenError("Session token is missing a subject claim")
    return str(subject)

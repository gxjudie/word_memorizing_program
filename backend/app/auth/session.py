"""The application's own signed session (App_Session), independent of Google.

An App_Session is an HS256 JWT signed with ``SESSION_SECRET`` carrying the
local user ID (``sub``), a random session ID (``sid``), and a 12 h expiry. It
is issued only after the Google ID token was validated. Logout records the
``sid`` in ``revoked_app_sessions`` until the session's ``exp`` (Req 9.13).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.revoked_session import RevokedAppSession

__all__ = [
    "SESSION_ALGORITHM",
    "SESSION_ISSUER",
    "SESSION_AUDIENCE",
    "DEFAULT_SESSION_TTL",
    "InvalidSessionError",
    "AppSession",
    "SessionClaims",
    "issue_app_session",
    "decode_app_session",
    "verify_app_session",
    "revoke_app_session",
    "is_app_session_revoked",
]

SESSION_ALGORITHM = "HS256"
SESSION_ISSUER = "japanese-vocab-trainer"
SESSION_AUDIENCE = "japanese-vocab-trainer-api"
DEFAULT_SESSION_TTL = timedelta(hours=12)


class InvalidSessionError(Exception):
    """The application session is malformed, expired, or untrusted."""


@dataclass(frozen=True)
class AppSession:
    """A first-party session returned after a successful Google sign-in."""

    user_id: str
    session_id: str
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class SessionClaims:
    """The verified contents of an App_Session token."""

    user_id: str
    session_id: str
    expires_at: datetime


def _secret(secret: str | None) -> str:
    value = secret if secret is not None else get_settings().session_secret
    if not value:
        raise ValueError("SESSION_SECRET is required")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_datetime(value: datetime) -> datetime:
    """The ORM ``DateTime`` columns are timezone-naive UTC."""

    return _utc(value).replace(tzinfo=None)


def issue_app_session(
    user_id: str,
    *,
    secret: str | None = None,
    expires_in: timedelta = DEFAULT_SESSION_TTL,
    now: datetime | None = None,
) -> AppSession:
    """Issue a short-lived first-party JWT carrying only app identity."""

    signing_secret = _secret(secret)
    issued_at = _utc(now or datetime.now(timezone.utc))
    expires_at = issued_at + expires_in
    session_id = secrets.token_urlsafe(24)
    claims = {
        "iss": SESSION_ISSUER,
        "aud": SESSION_AUDIENCE,
        "sub": user_id,
        "typ": "app_session",
        "sid": session_id,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(claims, signing_secret, algorithm=SESSION_ALGORITHM)
    return AppSession(
        user_id=user_id,
        session_id=session_id,
        token=token,
        expires_at=expires_at,
    )


def decode_app_session(token: str, *, secret: str | None = None) -> SessionClaims:
    """Verify signature, issuer, audience, type, and expiry; return claims.

    This does not consult the revocation list; callers that authorize
    requests must also check :func:`is_app_session_revoked`.
    """

    signing_secret = _secret(secret)
    try:
        claims = jwt.decode(
            token,
            signing_secret,
            algorithms=[SESSION_ALGORITHM],
            issuer=SESSION_ISSUER,
            audience=SESSION_AUDIENCE,
            options={"require_exp": True, "require_iat": True, "require_sub": True},
        )
    except JWTError:
        raise InvalidSessionError("Invalid or expired application session") from None
    if claims.get("typ") != "app_session":
        raise InvalidSessionError("Token is not an application session")
    subject = claims.get("sub")
    session_id = claims.get("sid")
    expiry = claims.get("exp")
    if not isinstance(subject, str) or not subject:
        raise InvalidSessionError("Application session is missing its subject")
    if not isinstance(session_id, str) or not session_id:
        raise InvalidSessionError("Application session is missing its session ID")
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
        raise InvalidSessionError("Application session is missing its expiry")
    return SessionClaims(
        user_id=subject,
        session_id=session_id,
        expires_at=datetime.fromtimestamp(expiry, tz=timezone.utc),
    )


def verify_app_session(token: str, *, secret: str | None = None) -> str:
    """Verify a first-party session and return its local user ID."""

    return decode_app_session(token, secret=secret).user_id


def revoke_app_session(
    db: Session,
    session_id: str,
    expires_at: datetime,
    *,
    now: datetime | None = None,
) -> None:
    """Record ``session_id`` as revoked until ``expires_at`` (Req 9.13).

    Idempotent: revoking an already revoked session is a no-op. Expired
    revocation rows are purged opportunistically.
    """

    if not session_id:
        raise ValueError("session_id is required")
    current = _db_datetime(now or datetime.now(timezone.utc))
    db.execute(delete(RevokedAppSession).where(RevokedAppSession.expires_at <= current))
    if db.get(RevokedAppSession, session_id) is None:
        db.add(
            RevokedAppSession(sid=session_id, expires_at=_db_datetime(expires_at))
        )
    db.commit()


def is_app_session_revoked(db: Session, session_id: str) -> bool:
    """Return whether ``session_id`` has been revoked by logout."""

    return (
        db.execute(
            select(RevokedAppSession.sid).where(RevokedAppSession.sid == session_id)
        ).first()
        is not None
    )

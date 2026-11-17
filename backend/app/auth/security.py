"""Compatibility exports for first-party application sessions.

Password authentication has been removed. New code should import from
``app.auth.session`` directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.auth.session import (
    DEFAULT_SESSION_TTL,
    InvalidSessionError,
    issue_app_session,
    verify_app_session,
)

InvalidTokenError = InvalidSessionError
JWT_ALGORITHM = "HS256"
DEFAULT_TOKEN_TTL = DEFAULT_SESSION_TTL


def issue_token(
    user_id: str,
    *,
    expires_in: timedelta | None = None,
    now: datetime | None = None,
) -> str:
    """Issue the app's own signed session token."""

    kwargs = {"now": now}
    if expires_in is not None:
        kwargs["expires_in"] = expires_in
    return issue_app_session(user_id, **kwargs).token


def verify_token(token: str) -> str:
    """Verify the app's own signed session token."""

    return verify_app_session(token)


__all__ = [
    "issue_token",
    "verify_token",
    "InvalidTokenError",
    "JWT_ALGORITHM",
    "DEFAULT_TOKEN_TTL",
]

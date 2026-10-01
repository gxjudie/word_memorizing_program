"""FastAPI dependencies for the API layer.

This module is the single place the HTTP layer wires itself to the business
layer (see the layer separation in the design: ``api/`` holds HTTP concerns
only and *calls* services). It provides, as reusable FastAPI dependencies:

* :func:`get_db` — a request-scoped SQLAlchemy ``Session`` (re-exported from
  :mod:`app.db.session` so routes depend on one import).
* :func:`get_current_user` — resolves the authenticated Learner from a Bearer
  JWT (``Authorization: Bearer <token>``). It verifies the token with
  :func:`app.auth.security.verify_token`, loads the ``User`` row, and raises
  ``401`` on any failure (missing/invalid/expired token or unknown user).
* :func:`get_word_service`, :func:`get_review_service`,
  :func:`get_sentence_service` — factory dependencies that construct the
  domain services with their collaborators already injected.

**Backend-mediated LLM access.** :func:`get_sentence_service` builds the
:class:`~app.services.sentence_generation.openai_adapter.OpenAIAdapter` with the
**server-held** OpenAI key from :class:`app.config.Settings`. The key lives only
inside the adapter; it is never a request/response field and never reaches the
client. Every route that generates a sentence depends on ``get_current_user``
first, so the proxy is only ever exercised by an authenticated Learner (Req 4.1,
auth model).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import InvalidTokenError, verify_token
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models.user import User
from app.services.sentence_generation.base import SentenceGenerationService
from app.services.sentence_generation.openai_adapter import OpenAIAdapter
from app.services.word_service import WordService

if TYPE_CHECKING:
    from app.services.review_service import ReviewService

__all__ = [
    "get_db",
    "bearer_scheme",
    "get_current_user",
    "get_word_service",
    "get_review_service",
    "get_sentence_service",
    "DbSession",
    "CurrentUser",
]

# Bearer scheme for OpenAPI docs and header extraction. ``auto_error=False`` so
# a missing/garbled Authorization header reaches :func:`get_current_user` as
# ``None`` and is turned into our own 401 (with a ``WWW-Authenticate`` header)
# rather than FastAPI's default 403.
bearer_scheme = HTTPBearer(auto_error=False, description="Bearer JWT session token")

# Reusable annotated alias for the DB session dependency.
DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    db: DbSession,
) -> User:
    """Resolve the authenticated Learner from a Bearer JWT.

    Extracts the token from the ``Authorization: Bearer <token>`` header,
    verifies its signature and expiry via
    :func:`app.auth.security.verify_token`, and loads the ``User`` whose id is
    the token subject.

    Args:
        credentials: The parsed Bearer credentials, or ``None`` when the header
            is absent or not a Bearer scheme.
        db: The request-scoped database session.

    Returns:
        The authenticated :class:`~app.models.user.User`.

    Raises:
        HTTPException: ``401 Unauthorized`` when the token is missing,
            malformed, expired, or does not correspond to a known user. The
            same status/message is used for every failure so callers cannot
            distinguish "no such user" from "bad token" (no account probing).
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None or not credentials.credentials:
        raise unauthorized

    try:
        user_id = verify_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise unauthorized from exc

    user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()
    if user is None:
        raise unauthorized

    return user


# Reusable annotated alias for the current-user dependency. Routes declare
# ``user: CurrentUser`` to require authentication.
CurrentUser = Annotated[User, Depends(get_current_user)]


def get_word_service(db: DbSession) -> WordService:
    """Provide a :class:`WordService` bound to the request's DB session."""
    return WordService(db)


def get_review_service(db: DbSession) -> "ReviewService":
    """Provide a :class:`ReviewService` bound to the request's DB session.

    The import is deferred to call time so this dependency module imports
    cleanly even while ``app.services.review_service`` is being implemented in
    parallel (task 7.1); the service is only needed when a review route is
    actually invoked.
    """
    from app.services.review_service import ReviewService

    return ReviewService(db)


def get_sentence_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SentenceGenerationService:
    """Provide the sentence-generation service (OpenAI adapter).

    The adapter is constructed with the application :class:`Settings`, from
    which it reads the **server-held** ``openai_api_key`` lazily when it first
    contacts the provider. The key is confined to the adapter and never exposed
    to the client (Req 4.1, backend-mediated LLM access).

    Returns:
        A :class:`SentenceGenerationService` ready to proxy generation calls.
    """
    return OpenAIAdapter(settings=settings)

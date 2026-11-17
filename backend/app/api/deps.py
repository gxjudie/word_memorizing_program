"""FastAPI dependencies for authentication, persistence, and services.

The API resolves each Learner from the application's own signed App_Session.
Google tokens never authenticate API requests; they are discarded after
sign-in. A session revoked by logout is rejected exactly like a missing one
(Req 9.13, 9.14).

Sentence generation uses the signed-in Learner's own Gemini_API_Key (BYOK):
``GeminiAdapter`` receives ``GeminiKeyService.get_api_key`` as its
``ApiKeyProvider``. There is no developer-held Gemini key anywhere in this
dependency graph (Req 10.16, 10.17).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.secrets import SecretCipher, SecretCipherConfigError
from app.auth.session import (
    InvalidSessionError,
    decode_app_session,
    is_app_session_revoked,
)
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models.gemini_credential import GeminiCredential
from app.models.user import User
from app.services.anki_import.import_service import AnkiImportService
from app.services.anki_import.preview_store import PreviewStore
from app.services.auth_service import AuthService
from app.services.gemini_key_service import GeminiKeyService
from app.services.review_service import ReviewService
from app.services.sentence_generation.base import SentenceGenerationService
from app.services.sentence_generation.gemini_adapter import GeminiAdapter
from app.services.word_service import WordService

__all__ = [
    "get_db",
    "bearer_scheme",
    "get_current_user",
    "get_auth_service",
    "get_secret_cipher",
    "get_gemini_http_client",
    "get_gemini_key_service",
    "get_word_service",
    "get_preview_store",
    "get_anki_import_service",
    "get_review_service",
    "get_sentence_service",
    "DbSession",
    "SettingsDep",
    "CurrentUser",
    "AuthServiceDep",
    "GeminiKeyServiceDep",
    "KEY_STORAGE_UNAVAILABLE",
]

# The Bearer value is the app's own short-lived session token, issued only after
# a validated Google ID token. It is never a Google access token.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Application session token",
)

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    db: DbSession,
    settings: SettingsDep,
) -> User:
    """Resolve a Learner from a valid, non-revoked App_Session.

    Missing, invalid, expired, revoked, and unknown-user sessions all get the
    same 401 with no data (Req 9.13, 9.14), so a caller cannot tell them apart.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or not credentials.credentials:
        raise unauthorized
    try:
        claims = decode_app_session(
            credentials.credentials,
            secret=settings.session_secret,
        )
    except InvalidSessionError:
        raise unauthorized from None
    if is_app_session_revoked(db, claims.session_id):
        raise unauthorized
    user = db.execute(
        select(User).where(User.id == claims.user_id)
    ).scalar_one_or_none()
    if user is None:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_auth_service(db: DbSession, settings: SettingsDep) -> AuthService:
    """Provide the request-scoped Google sign-in and logout service."""
    return AuthService(db, settings=settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


# Fixed error body when TOKEN_ENCRYPTION_KEY is missing or invalid. Stored keys
# can then be neither encrypted nor decrypted, so key-dependent routes answer
# 503 instead of failing with an unhandled error (design: configuration).
KEY_STORAGE_UNAVAILABLE = {
    "code": "key_storage_unavailable",
    "message": "Gemini API key storage is not configured on the server.",
}


def get_secret_cipher(settings: SettingsDep) -> SecretCipher:
    """Provide the Fernet cipher keyed by ``TOKEN_ENCRYPTION_KEY``.

    Raises a 503 with a fixed, secret-free body when the key is missing or
    is not a valid Fernet key.
    """
    try:
        return SecretCipher(settings.token_encryption_key)
    except SecretCipherConfigError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=KEY_STORAGE_UNAVAILABLE,
        ) from None


@lru_cache
def get_gemini_http_client() -> httpx.AsyncClient:
    """Shared HTTP client for all Gemini calls (created on first use)."""
    return httpx.AsyncClient()


def get_gemini_key_service(
    db: DbSession,
    cipher: Annotated[SecretCipher, Depends(get_secret_cipher)],
    http_client: Annotated[httpx.AsyncClient, Depends(get_gemini_http_client)],
) -> GeminiKeyService:
    """Provide the request-scoped GeminiKeyService.

    It shares the request's DB session, the Fernet cipher, and the process-wide
    Gemini HTTP client. Its per-user verification locks are process-wide too,
    so concurrent requests from one Learner see the same lock (Req 10.7).
    """
    return GeminiKeyService(db, cipher=cipher, http_client=http_client)


GeminiKeyServiceDep = Annotated[GeminiKeyService, Depends(get_gemini_key_service)]


def get_word_service(db: DbSession) -> WordService:
    """Provide a WordService bound to the request's database session.

    Word routes work without a stored Gemini key (Req 10.2).
    """
    return WordService(db)


@lru_cache
def get_preview_store() -> PreviewStore:
    """The process-wide in-memory Import_Preview store (Req 2.35-2.37, 2.42).

    One instance per process, so every request sees the same previews and the
    same per-account import locks.
    """
    return PreviewStore()


def get_anki_import_service(
    db: DbSession,
    store: Annotated[PreviewStore, Depends(get_preview_store)],
    words: Annotated[WordService, Depends(get_word_service)],
) -> AnkiImportService:
    """Provide the request-scoped Anki import service.

    Import needs an App_Session but no Gemini key.
    """
    return AnkiImportService(db, store, words)


def _stored_gemini_key_check(db: Session) -> Callable[[str], bool]:
    """Return a key-presence check over ``gemini_credentials``.

    This is the same check as ``GeminiKeyService.has_key``. It reads only
    whether a row exists and never decrypts, so it does not depend on
    ``TOKEN_ENCRYPTION_KEY`` being configured.
    """

    def has_gemini_key(user_id: str) -> bool:
        return (
            db.execute(
                select(GeminiCredential.id).where(GeminiCredential.user_id == user_id)
            ).first()
            is not None
        )

    return has_gemini_key


def get_review_service(db: DbSession) -> ReviewService:
    """Provide a ReviewService with the Learner key-presence check (Req 10.1)."""
    return ReviewService(db, has_gemini_key=_stored_gemini_key_check(db))


def get_sentence_service(
    settings: SettingsDep,
    key_service: GeminiKeyServiceDep,
    http_client: Annotated[httpx.AsyncClient, Depends(get_gemini_http_client)],
) -> SentenceGenerationService:
    """Provide the Gemini adapter for the configured ``GEMINI_MODEL``.

    Its ``ApiKeyProvider`` is ``GeminiKeyService.get_api_key``, so each call
    uses only the signed-in Learner's own decrypted key, and raises
    ``MissingGeminiKeyError`` before any request when none is stored (Req 4.7).
    """
    return GeminiAdapter(
        api_key_provider=key_service.get_api_key,
        http_client=http_client,
        model=settings.gemini_model,
    )

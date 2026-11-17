"""GeminiKeyService: connect, verify, store, read, and remove a Learner's key.

Implements Requirement 10.5-10.15 (BYOK):

- **Format check first.** :meth:`GeminiKeyService.connect` trims and
  format-checks the submitted value. A format failure stores nothing and
  sends no request (Req 10.5, 10.6).
- **One verification at a time per Learner.** A per-user non-blocking lock is
  taken before verification; a concurrent submission raises
  :class:`KeyVerificationInProgressError` (Req 10.7).
- **Verification.** Exactly one ``GET .../v1beta/models?pageSize=1`` with the
  ``x-goog-api-key`` header and a 10 s total timeout. It generates no content.
  The response is classified by :func:`classify_verification`: ``VALID`` and
  ``QUOTA_EXHAUSTED`` store the key (Req 10.10, 10.11); ``INVALID`` raises
  :class:`KeyRejectedError` (Req 10.8); ``UNAVAILABLE`` raises
  :class:`KeyVerificationUnavailableError` (Req 10.9).
- **Replace atomically.** The Fernet ciphertext is upserted into
  ``gemini_credentials`` in one transaction. Nothing is written on any
  failure, so a previously stored key stays in use (Req 10.14).
- **Secrecy.** The key is never logged and never appears in an exception
  message. httpx errors are converted to this module's fixed-message
  exceptions; nothing is raised from inside an ``except`` block that could
  carry provider text (Req 10.12, 10.13).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import ClassVar

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.secrets import (
    KeyFormatError,
    SecretCipher,
    SecretDecryptionError,
    key_hint,
    normalize_gemini_key,
)
from app.models.gemini_credential import GeminiCredential
from app.services.gemini_errors import VerificationOutcome, classify_verification
from app.services.sentence_generation.base import MissingGeminiKeyError

__all__ = [
    "GEMINI_VERIFY_URL",
    "VERIFY_TIMEOUT_SECONDS",
    "GeminiKeyConnectResult",
    "GeminiKeyError",
    "GeminiKeyService",
    "GeminiKeyStatus",
    "KeyFormatError",
    "KeyRejectedError",
    "KeyStorageError",
    "KeyVerificationInProgressError",
    "KeyVerificationUnavailableError",
    "VerificationLocks",
]

logger = logging.getLogger(__name__)

GEMINI_VERIFY_URL: str = "https://generativelanguage.googleapis.com/v1beta/models"
VERIFY_TIMEOUT_SECONDS: float = 10.0


@dataclass(frozen=True)
class GeminiKeyStatus:
    """Browser-safe key status: whether a key is stored and its last 4 chars."""

    connected: bool
    key_hint: str | None


@dataclass(frozen=True)
class GeminiKeyConnectResult:
    """Outcome of a successful connect (Req 10.10, 10.11)."""

    key_hint: str
    quota_exhausted_notice: bool


class GeminiKeyError(Exception):
    """Base class for key-service failures. Messages never contain a key."""

    default_message: ClassVar[str] = "The Gemini API key could not be connected."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message if message is not None else self.default_message)


class KeyRejectedError(GeminiKeyError):
    """Gemini reported the key as invalid (Req 10.8) -> ``400 invalid_key``."""

    default_message: ClassVar[str] = "The Gemini API key was rejected."


class KeyVerificationUnavailableError(GeminiKeyError):
    """Timeout, transport, or server error during verification (Req 10.9)
    -> ``503 could_not_verify``."""

    default_message: ClassVar[str] = "The Gemini API key could not be verified now."


class KeyVerificationInProgressError(GeminiKeyError):
    """A verification is already in flight for this Learner (Req 10.7)
    -> ``409 key_verification_in_progress``."""

    default_message: ClassVar[str] = "A key verification is already in progress."


class KeyStorageError(GeminiKeyError):
    """The verified key could not be written; nothing was stored."""

    default_message: ClassVar[str] = "The Gemini API key could not be saved."


class VerificationLocks:
    """Per-user, non-blocking "verification in flight" markers.

    Shared across request-scoped service instances so a second submission from
    the same Learner is refused while the first is being verified.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._in_flight: set[str] = set()

    def try_acquire(self, user_id: str) -> bool:
        """Mark ``user_id`` as verifying; return ``False`` if already marked."""
        with self._guard:
            if user_id in self._in_flight:
                return False
            self._in_flight.add(user_id)
            return True

    def release(self, user_id: str) -> None:
        """Clear the in-flight marker for ``user_id``."""
        with self._guard:
            self._in_flight.discard(user_id)

    def is_held(self, user_id: str) -> bool:
        """Return whether a verification is in flight for ``user_id``."""
        with self._guard:
            return user_id in self._in_flight

    @contextmanager
    def hold(self, user_id: str) -> Iterator[None]:
        """Hold the marker for the block or raise ``KeyVerificationInProgressError``."""
        if not self.try_acquire(user_id):
            raise KeyVerificationInProgressError()
        try:
            yield
        finally:
            self.release(user_id)


# Process-wide registry used when no registry is injected.
_DEFAULT_LOCKS = VerificationLocks()


class GeminiKeyService:
    """Store and serve each Learner's own encrypted Gemini_API_Key."""

    def __init__(
        self,
        db: Session,
        *,
        cipher: SecretCipher,
        http_client: httpx.AsyncClient,
        locks: VerificationLocks | None = None,
        verify_timeout: float = VERIFY_TIMEOUT_SECONDS,
    ) -> None:
        """Bind the DB session, Fernet cipher, and shared HTTP client."""
        self._db = db
        self._cipher = cipher
        self._http = http_client
        self._locks = locks if locks is not None else _DEFAULT_LOCKS
        self._verify_timeout = verify_timeout

    # ---- reads -----------------------------------------------------------

    def _credential(self, user_id: str) -> GeminiCredential | None:
        return self._db.execute(
            select(GeminiCredential).where(GeminiCredential.user_id == user_id)
        ).scalar_one_or_none()

    def status(self, user_id: str) -> GeminiKeyStatus:
        """Return ``{connected, key_hint}`` for the Learner (Req 10.12)."""
        credential = self._credential(user_id)
        if credential is None:
            return GeminiKeyStatus(connected=False, key_hint=None)
        return GeminiKeyStatus(connected=True, key_hint=credential.key_hint)

    def has_key(self, user_id: str) -> bool:
        """Return whether a key is stored, without decrypting it."""
        return self._credential(user_id) is not None

    def get_api_key(self, user_id: str) -> str:
        """Decrypt and return the Learner's key for server-side use only.

        Raises :class:`MissingGeminiKeyError` when no key is stored, or when the
        stored ciphertext can no longer be decrypted (for example after
        ``TOKEN_ENCRYPTION_KEY`` was rotated); in both cases the Learner must
        connect a key again.
        """
        credential = self._credential(user_id)
        if credential is None:
            raise MissingGeminiKeyError()
        try:
            return self._cipher.decrypt(credential.api_key_encrypted)
        except SecretDecryptionError:
            logger.warning("Stored Gemini key for user %s could not be decrypted", user_id)
        raise MissingGeminiKeyError()

    # ---- writes ----------------------------------------------------------

    async def connect(self, user_id: str, raw_key: str) -> GeminiKeyConnectResult:
        """Trim, format-check, verify once, then encrypt and upsert the key.

        Raises:
            KeyFormatError: Empty or malformed key; no request sent (Req 10.6).
            KeyVerificationInProgressError: Another verification for this
                Learner is in flight (Req 10.7).
            KeyRejectedError: Gemini reported the key invalid (Req 10.8).
            KeyVerificationUnavailableError: Timeout, transport, or server
                error (Req 10.9).
            KeyStorageError: The database write failed; nothing stored.
        """
        key = normalize_gemini_key(raw_key)
        with self._locks.hold(user_id):
            outcome = await self._verify(key)
            logger.info(
                "Gemini key verification for user %s: %s", user_id, outcome.value
            )
            if outcome is VerificationOutcome.INVALID:
                raise KeyRejectedError()
            if outcome is VerificationOutcome.UNAVAILABLE:
                raise KeyVerificationUnavailableError()
            hint = self._store(user_id, key)
        return GeminiKeyConnectResult(
            key_hint=hint,
            quota_exhausted_notice=outcome is VerificationOutcome.QUOTA_EXHAUSTED,
        )

    def remove(self, user_id: str) -> GeminiKeyStatus:
        """Delete the stored key (Req 10.15) and return the not-connected status."""
        try:
            self._db.execute(
                delete(GeminiCredential).where(GeminiCredential.user_id == user_id)
            )
            self._db.commit()
        except SQLAlchemyError:
            self._db.rollback()
            failure = KeyStorageError("The Gemini API key could not be removed.")
        else:
            return GeminiKeyStatus(connected=False, key_hint=None)
        raise failure

    # ---- internals -------------------------------------------------------

    async def _verify(self, key: str) -> VerificationOutcome:
        """Send exactly one no-content verification request (Req 10.7, 10.9)."""
        status: int | None = None
        body: bytes | None = None
        try:
            response = await asyncio.wait_for(
                self._http.get(
                    GEMINI_VERIFY_URL,
                    params={"pageSize": 1},
                    headers={"x-goog-api-key": key},
                    timeout=self._verify_timeout,
                ),
                timeout=self._verify_timeout,
            )
        except (asyncio.TimeoutError, TimeoutError, httpx.HTTPError):
            # Timeout or transport failure; the httpx exception (which can carry
            # the request and its headers) is dropped here.
            pass
        else:
            status, body = response.status_code, response.content
        return classify_verification(status, body)

    def _store(self, user_id: str, key: str) -> str:
        """Encrypt and upsert the key in one transaction; return its hint."""
        hint = key_hint(key)
        ciphertext = self._cipher.encrypt(key)
        try:
            credential = self._credential(user_id)
            if credential is None:
                self._db.add(
                    GeminiCredential(
                        user_id=user_id,
                        api_key_encrypted=ciphertext,
                        key_hint=hint,
                    )
                )
            else:
                credential.api_key_encrypted = ciphertext
                credential.key_hint = hint
            self._db.commit()
        except SQLAlchemyError:
            # The SQLAlchemy error can echo bound parameters, so it is dropped.
            self._db.rollback()
            stored = False
        else:
            stored = True
        if not stored:
            raise KeyStorageError()
        return hint

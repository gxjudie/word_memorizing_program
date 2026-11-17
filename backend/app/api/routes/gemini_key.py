"""Gemini_API_Key HTTP routes (Req 10.5-10.15, 9.14).

HTTP layer only: every rule lives in :class:`GeminiKeyService`. Each route
requires a valid, non-revoked App_Session (``CurrentUser`` is declared first,
so an unauthenticated caller gets ``401`` before anything else is resolved).
When ``TOKEN_ENCRYPTION_KEY`` is missing or invalid, the cipher dependency
answers ``503 key_storage_unavailable``.

Endpoints (design API table):

* ``GET /api/gemini-key`` -> ``{connected, key_hint}``
* ``PUT /api/gemini-key`` with ``{api_key}`` ->
  ``200 {connected: true, key_hint, quota_exhausted_notice}``,
  ``400 invalid_key_format``, ``400 invalid_key``,
  ``409 key_verification_in_progress``, ``503 could_not_verify``
* ``DELETE /api/gemini-key`` -> ``200 {connected: false, key_hint: null}``

Error bodies are ``{"detail": {"code", "message"}}`` with fixed messages. The
submitted key never appears in a response, an error, or a log (Req 10.12,
10.13); service exceptions are re-raised ``from None`` so no chained context
carries it either.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, GeminiKeyServiceDep
from app.auth.secrets import KeyFormatError
from app.schemas.gemini_key import GeminiKeyConnectOut, GeminiKeyIn, GeminiKeyStatusOut
from app.services.gemini_key_service import (
    GeminiKeyError,
    KeyRejectedError,
    KeyStorageError,
    KeyVerificationInProgressError,
    KeyVerificationUnavailableError,
)

__all__ = ["router"]

router = APIRouter()

# Fixed, key-free error bodies.
INVALID_KEY_FORMAT = {
    "code": "invalid_key_format",
    "message": "The Gemini API key format is invalid. Paste the full key exactly as shown in Google AI Studio.",
}
INVALID_KEY = {
    "code": "invalid_key",
    "message": "Gemini rejected this API key. Check the key and try again.",
}
VERIFICATION_IN_PROGRESS = {
    "code": "key_verification_in_progress",
    "message": "A key verification is already in progress. Wait for it to finish.",
}
COULD_NOT_VERIFY = {
    "code": "could_not_verify",
    "message": "The Gemini API key could not be verified right now. Try again later.",
}
KEY_STORAGE_FAILED = {
    "code": "key_storage_failed",
    "message": "The Gemini API key change could not be saved. Try again.",
}

_CONNECT_ERRORS: dict[type[GeminiKeyError], tuple[int, dict[str, str]]] = {
    KeyRejectedError: (status.HTTP_400_BAD_REQUEST, INVALID_KEY),
    KeyVerificationInProgressError: (status.HTTP_409_CONFLICT, VERIFICATION_IN_PROGRESS),
    KeyVerificationUnavailableError: (status.HTTP_503_SERVICE_UNAVAILABLE, COULD_NOT_VERIFY),
    KeyStorageError: (status.HTTP_500_INTERNAL_SERVER_ERROR, KEY_STORAGE_FAILED),
}


@router.get("", response_model=GeminiKeyStatusOut, summary="Gemini API key status")
def get_gemini_key_status(
    user: CurrentUser, service: GeminiKeyServiceDep
) -> GeminiKeyStatusOut:
    """Return whether a key is stored and its last-4 hint (Req 10.11, 10.12)."""
    current = service.status(user.id)
    return GeminiKeyStatusOut(connected=current.connected, key_hint=current.key_hint)


@router.put("", response_model=GeminiKeyConnectOut, summary="Connect or replace the Gemini API key")
async def put_gemini_key(
    user: CurrentUser, service: GeminiKeyServiceDep, body: GeminiKeyIn
) -> GeminiKeyConnectOut:
    """Trim, format-check, verify once, and store the key (Req 10.5-10.11, 10.14).

    Nothing is stored on any failure, so a previously stored key stays in use.
    """
    error: tuple[int, dict[str, str]] | None = None
    try:
        result = await service.connect(user.id, body.api_key.get_secret_value())
    except KeyFormatError:
        error = (status.HTTP_400_BAD_REQUEST, INVALID_KEY_FORMAT)
    except GeminiKeyError as exc:
        error = _CONNECT_ERRORS.get(
            type(exc), (status.HTTP_503_SERVICE_UNAVAILABLE, COULD_NOT_VERIFY)
        )
    if error is not None:
        # Raised outside the ``except`` block so no exception context is chained.
        raise HTTPException(status_code=error[0], detail=error[1])
    return GeminiKeyConnectOut(
        key_hint=result.key_hint,
        quota_exhausted_notice=result.quota_exhausted_notice,
    )


@router.delete("", response_model=GeminiKeyStatusOut, summary="Remove the Gemini API key")
def delete_gemini_key(
    user: CurrentUser, service: GeminiKeyServiceDep
) -> GeminiKeyStatusOut:
    """Delete the stored key and return the not-connected status (Req 10.15)."""
    try:
        current = service.remove(user.id)
    except KeyStorageError:
        current = None
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=KEY_STORAGE_FAILED
        )
    return GeminiKeyStatusOut(connected=current.connected, key_hint=current.key_hint)

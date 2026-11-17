"""Pure classifiers for Gemini API error responses.

Gemini reports failures as an HTTP status plus a Google RPC error body::

    {"error": {"code": 400, "message": "...", "status": "INVALID_ARGUMENT",
               "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "API_KEY_INVALID", ...}]}}

The functions here only inspect the status, ``error.status``, and the
``ErrorInfo.reason`` values. They never read, return, or log the error
``message``, so a classification result cannot carry provider text or a key.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class GeminiFailure(Enum):
    """How a non-2xx ``generateContent`` response affects the request."""

    INVALID_KEY = "invalid_key"  # Req 4.9: stop, no further attempts
    QUOTA = "quota"  # Req 4.8: stop, no further attempts
    TRANSIENT = "transient"  # Req 4.11: retry within the same allowance


@dataclass(frozen=True)
class _RpcError:
    """The parts of a Google RPC error body used for classification."""

    status: str | None
    reasons: frozenset[str]


def _parse_rpc_error(body: bytes | None) -> _RpcError:
    """Extract ``error.status`` and every ``details[].reason`` from ``body``.

    A missing, non-JSON, or unexpectedly shaped body yields an empty result.
    """
    if not body:
        return _RpcError(status=None, reasons=frozenset())
    try:
        payload: Any = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return _RpcError(status=None, reasons=frozenset())
    if isinstance(payload, list) and payload:
        # Some Google endpoints wrap the error object in a one-element list.
        payload = payload[0]
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return _RpcError(status=None, reasons=frozenset())

    status = error.get("status")
    reasons: set[str] = set()
    details = error.get("details")
    if isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict) and isinstance(detail.get("reason"), str):
                reasons.add(detail["reason"])
    return _RpcError(
        status=status if isinstance(status, str) else None,
        reasons=frozenset(reasons),
    )


def classify_generation_failure(status: int, body: bytes | None) -> GeminiFailure:
    """Classify a non-2xx ``generateContent`` response.

    - 429, or ``error.status == "RESOURCE_EXHAUSTED"`` -> ``QUOTA`` (Req 4.8)
    - 400 with ``ErrorInfo.reason == "API_KEY_INVALID"``, 401, or 403
      (``PERMISSION_DENIED``) -> ``INVALID_KEY`` (Req 4.9)
    - anything else (other 4xx, 5xx) -> ``TRANSIENT`` (Req 4.11)

    Transport errors never reach this function; the adapter treats them as
    transient directly.
    """
    rpc = _parse_rpc_error(body)
    if status == 429 or rpc.status == "RESOURCE_EXHAUSTED":
        return GeminiFailure.QUOTA
    if status == 400 and "API_KEY_INVALID" in rpc.reasons:
        return GeminiFailure.INVALID_KEY
    if status in (401, 403):
        # Google maps HTTP 403 to PERMISSION_DENIED and 401 to UNAUTHENTICATED;
        # both mean the key is invalid, revoked, or not allowed to call Gemini.
        return GeminiFailure.INVALID_KEY
    return GeminiFailure.TRANSIENT


class VerificationOutcome(Enum):
    """Result of the key-verification request ``GET v1beta/models?pageSize=1``."""

    VALID = "valid"  # Req 10.11: store
    INVALID = "invalid"  # Req 10.8: reject, store nothing
    QUOTA_EXHAUSTED = "quota"  # Req 10.10: store, show the quota notice
    UNAVAILABLE = "unavailable"  # Req 10.9: could not verify, store nothing


def classify_verification(
    status: int | None, body: bytes | None
) -> VerificationOutcome:
    """Classify the key-verification response.

    ``status`` is ``None`` for a timeout or transport failure.

    - 200 -> ``VALID``
    - 400 with ``ErrorInfo.reason == "API_KEY_INVALID"``, 401, or 403
      -> ``INVALID`` (Req 10.8)
    - 429, or ``error.status == "RESOURCE_EXHAUSTED"`` -> ``QUOTA_EXHAUSTED``
      (Req 10.10)
    - anything else (other 2xx/4xx, 5xx, ``None``) -> ``UNAVAILABLE`` (Req 10.9)

    An explicit invalid-key signal is checked before the quota signal, so a
    response carrying both never causes a rejected key to be stored.
    """
    if status is None:
        return VerificationOutcome.UNAVAILABLE
    if status == 200:
        return VerificationOutcome.VALID
    rpc = _parse_rpc_error(body)
    if status == 400 and "API_KEY_INVALID" in rpc.reasons:
        return VerificationOutcome.INVALID
    if status in (401, 403):
        return VerificationOutcome.INVALID
    if status == 429 or rpc.status == "RESOURCE_EXHAUSTED":
        return VerificationOutcome.QUOTA_EXHAUSTED
    return VerificationOutcome.UNAVAILABLE


__all__ = [
    "GeminiFailure",
    "VerificationOutcome",
    "classify_generation_failure",
    "classify_verification",
]

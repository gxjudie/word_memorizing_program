"""Request/response schemas for ``/api/gemini-key``.

The submitted key is a :class:`~pydantic.SecretStr`, so it is masked in
``repr``/``str`` and in model dumps. Responses carry only the last-4
``key_hint``, never the key itself (Req 10.12).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr

__all__ = ["GeminiKeyIn", "GeminiKeyStatusOut", "GeminiKeyConnectOut"]


class GeminiKeyIn(BaseModel):
    """Body of ``PUT /api/gemini-key``. Trimming and format checks happen in
    :func:`app.auth.secrets.normalize_gemini_key` (Req 10.5, 10.6)."""

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr


class GeminiKeyStatusOut(BaseModel):
    """Connected status: ``GET`` / ``DELETE /api/gemini-key`` and ``/me``."""

    connected: bool
    key_hint: str | None = None


class GeminiKeyConnectOut(BaseModel):
    """Successful ``PUT /api/gemini-key`` (Req 10.10, 10.11)."""

    connected: Literal[True] = True
    key_hint: str
    quota_exhausted_notice: bool = False

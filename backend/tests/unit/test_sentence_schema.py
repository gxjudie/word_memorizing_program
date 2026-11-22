"""Unit tests for the generated-sentence schemas.

Covers the 1-50 token bound that GeneratedSentenceSchema enforces in Pydantic,
and confirms the Gemini responseSchema intentionally omits array length limits
(Gemini rejects minItems/maxItems on the nested tokens array with HTTP 400
INVALID_ARGUMENT). The bound therefore lives only in Pydantic re-validation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.sentence import GEMINI_RESPONSE_SCHEMA, GeneratedSentenceSchema


def _token(surface: str = "勉強", *, is_target: bool = True) -> dict:
    return {
        "surface": surface,
        "reading": "べんきょう",
        "meaning": "study",
        "is_target": is_target,
        "contains_kanji": True,
    }


def _sentence_with(n_tokens: int) -> dict:
    # First token is the target; the rest are fillers so at least one is_target
    # token is always present when n_tokens >= 1.
    tokens = [_token()]
    tokens += [_token("毎", is_target=False) for _ in range(n_tokens - 1)]
    return {"text": "勉強する。", "tokens": tokens[:n_tokens]}


def test_zero_tokens_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GeneratedSentenceSchema.model_validate({"text": "x", "tokens": []})


def test_fifty_tokens_is_accepted() -> None:
    result = GeneratedSentenceSchema.model_validate(_sentence_with(50))
    assert len(result.tokens) == 50


def test_fifty_one_tokens_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GeneratedSentenceSchema.model_validate(_sentence_with(51))


def test_gemini_schema_omits_array_length_limits() -> None:
    # Gemini rejects minItems/maxItems on this nested array; the bound is
    # enforced by GeneratedSentenceSchema instead (see tests above).
    tokens_schema = GEMINI_RESPONSE_SCHEMA["properties"]["tokens"]
    assert "minItems" not in tokens_schema
    assert "maxItems" not in tokens_schema

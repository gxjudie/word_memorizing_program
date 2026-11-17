"""Unit tests for the SentenceGenerationService contract (task 6.1)."""

from __future__ import annotations

import asyncio
import inspect

import pytest

from app.services.sentence_generation import base
from app.services.sentence_generation.base import (
    ApiKeyProvider,
    GenerationError,
    GenerationTimeout,
    InvalidGeminiKeyError,
    MissingGeminiKeyError,
    QuotaExceededError,
    SentenceGenerationError,
    SentenceGenerationService,
)

_ERROR_TYPES = [
    MissingGeminiKeyError,
    InvalidGeminiKeyError,
    QuotaExceededError,
    GenerationTimeout,
    GenerationError,
]


class _SentenceService(SentenceGenerationService):
    def __init__(self, key_provider: ApiKeyProvider) -> None:
        self._key_provider = key_provider

    async def generate(self, user_id, target, previous):
        key = self._key_provider(user_id)
        return user_id, target, previous, key


def test_generate_contract_accepts_learner_identity_target_and_previous():
    parameters = list(inspect.signature(SentenceGenerationService.generate).parameters)
    assert parameters == ["self", "user_id", "target", "previous"]
    assert inspect.iscoroutinefunction(SentenceGenerationService.generate)


def test_service_is_abstract():
    with pytest.raises(TypeError):
        SentenceGenerationService()  # type: ignore[abstract]


def test_api_key_provider_is_called_with_user_id():
    calls: list[str] = []

    def provider(user_id: str) -> str:
        calls.append(user_id)
        return "AIza-test"

    target = object()
    result = asyncio.run(_SentenceService(provider).generate("learner-123", target, None))
    assert result == ("learner-123", target, None, "AIza-test")
    assert calls == ["learner-123"]


def test_missing_key_from_provider_propagates():
    def provider(_user_id: str) -> str:
        raise MissingGeminiKeyError()

    with pytest.raises(MissingGeminiKeyError):
        asyncio.run(_SentenceService(provider).generate("learner-123", object(), None))


@pytest.mark.parametrize("error_type", _ERROR_TYPES)
def test_errors_share_the_generation_base(error_type):
    assert issubclass(error_type, SentenceGenerationError)


@pytest.mark.parametrize("error_type", _ERROR_TYPES)
def test_default_messages_are_fixed_and_non_empty(error_type):
    first, second = str(error_type()), str(error_type())
    assert first and first == second
    assert "AIza" not in first


def test_default_messages_are_distinct_per_error():
    messages = {str(error_type()) for error_type in _ERROR_TYPES}
    assert len(messages) == len(_ERROR_TYPES)


def test_openai_plan_usage_errors_are_removed():
    for name in ("PlanIneligibleError", "UsageCapExceededError", "TokenRefreshError",
                 "AccessTokenProvider"):
        assert not hasattr(base, name)
        assert name not in base.__all__

"""Gemini ``generateContent`` adapter for sentence generation (BYOK).

The adapter asks the injected :class:`ApiKeyProvider` for the signed-in
Learner's own Gemini_API_Key once per generation request and sends it only in
the ``x-goog-api-key`` header. There is no developer-held key (Req 10.16).

Pipeline per request (Req 4.1-4.11):

- At most ``1 + MAX_RETRIES`` (= 4) attempts. Each attempt is one
  ``POST .../models/{model}:generateContent`` bounded at 5 s by
  :func:`asyncio.wait_for`. A timeout ends the request with
  :class:`GenerationTimeout`, and the late response is dropped (Req 4.2, 4.5).
- A 429 / ``RESOURCE_EXHAUSTED`` response raises :class:`QuotaExceededError`
  and an invalid-key response raises :class:`InvalidGeminiKeyError`, both with
  no further attempts (Req 4.8, 4.9).
- Other 4xx/5xx responses, transport errors, and discarded outputs (no
  candidates, a ``promptFeedback.blockReason``, a ``finishReason`` other than
  ``STOP``, invalid JSON, a schema failure, no target token, 0 or more than 50
  tokens, or no new non-target word) use the retry allowance. Running out of
  it raises :class:`GenerationError` (Req 4.6, 4.10, 4.11).

Secrecy (Req 10.13): the key is never logged or put in an exception message.
httpx errors are re-raised as this module's own fixed-message exception types
with ``from None``, so the original exception and its context are dropped.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import ValidationError

from app.schemas.sentence import (
    GEMINI_RESPONSE_SCHEMA,
    GeneratedSentence,
    GeneratedSentenceSchema,
)
from app.services.gemini_errors import GeminiFailure, classify_generation_failure
from app.services.sentence_generation.base import (
    ApiKeyProvider,
    GenerationError,
    GenerationTimeout,
    InvalidGeminiKeyError,
    MissingGeminiKeyError,
    QuotaExceededError,
    SentenceGenerationService,
)

if TYPE_CHECKING:
    from app.models.word_entry import WordEntry


GEMINI_API_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL: str = "gemini-flash-lite-latest"
TIME_BUDGET_SECONDS: float = 5.0
MAX_RETRIES: int = 3

# Model IDs are interpolated into the URL path, so only allow the characters
# Gemini model IDs actually use.
_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

SYSTEM_INSTRUCTION: str = "\n".join(
    [
        "You write natural Japanese example sentences for a vocabulary trainer.",
        "Return only JSON that matches the response schema.",
        "Rules:",
        "- The sentence must contain the target word, either exactly as given "
        "or in an inflected form whose dictionary form is the target word.",
        "- Split the sentence into between 1 and 50 word tokens. Joining the "
        "token surfaces must reproduce the sentence text.",
        "- For each token give its hiragana reading and a short English meaning.",
        "- Set is_target to true only for the token(s) that are the target word.",
        "- Set contains_kanji to true for tokens that contain kanji.",
        "- If a previous sentence is given, the new sentence must use at least "
        "one word, other than the target word, that does not appear in the "
        "previous sentence.",
    ]
)


def build_user_prompt(target: "WordEntry", previous: GeneratedSentence | None) -> str:
    """Build the user prompt carrying the target and the previous sentence."""
    lines = [
        f"Target word: {json.dumps(target.word_form, ensure_ascii=False)}",
        f"Reading: {json.dumps(target.reading, ensure_ascii=False)}",
        f"Meaning: {json.dumps(target.meaning, ensure_ascii=False)}",
    ]
    if previous is not None:
        lines.append(
            f"Previous sentence: {json.dumps(previous.text, ensure_ascii=False)}"
        )
    return "\n".join(lines)


def build_request_body(target: "WordEntry", previous: GeneratedSentence | None) -> dict:
    """Build the ``generateContent`` JSON body for one attempt."""
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [
            {"role": "user", "parts": [{"text": build_user_prompt(target, previous)}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": GEMINI_RESPONSE_SCHEMA,
        },
    }


def extract_candidate_text(payload: Any) -> str | None:
    """Return ``candidates[0].content.parts[0].text``, or ``None`` to discard.

    A blocked prompt, no candidates, a ``finishReason`` other than ``STOP``, or
    a missing text part all count as a discarded output.
    """
    if not isinstance(payload, dict):
        return None
    feedback = payload.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        return None
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    candidate = candidates[0]
    if not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP":
        return None
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
        return None
    text = parts[0].get("text")
    return text if isinstance(text, str) else None


def parse_sentence(text: str) -> GeneratedSentence | None:
    """Parse and re-validate untrusted model text; ``None`` means discard.

    ``GeneratedSentenceSchema`` enforces target presence (Req 4.1, 4.6) and the
    1-50 token range (Req 4.4).
    """
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    try:
        return GeneratedSentenceSchema.model_validate(payload)
    except ValidationError:
        return None


def differs_from_previous(
    candidate: GeneratedSentence, previous: GeneratedSentence | None
) -> bool:
    """Req 4.3: some non-target word of ``candidate`` is absent from ``previous``."""
    if previous is None:
        return True
    previous_words = {token.surface for token in previous.tokens}
    return any(
        not token.is_target and token.surface not in previous_words
        for token in candidate.tokens
    )


class GeminiAdapter(SentenceGenerationService):
    """Generate validated sentences with the Learner's own Gemini_API_Key."""

    def __init__(
        self,
        api_key_provider: ApiKeyProvider,
        http_client: httpx.AsyncClient,
        *,
        model: str = DEFAULT_MODEL,
        time_budget_seconds: float = TIME_BUDGET_SECONDS,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        """Create an adapter with injected key source and HTTP client.

        Args:
            api_key_provider: Returns the Learner's decrypted key; raises
                :class:`MissingGeminiKeyError` when none is stored.
            http_client: Shared client; tests pass one on ``httpx.MockTransport``.
            model: Gemini model ID from ``GEMINI_MODEL``.
            time_budget_seconds: Per-attempt wall-clock limit (Req 4.2).
            max_retries: Additional attempts after the first (Req 4.6).
        """
        if not _MODEL_ID_PATTERN.fullmatch(model):
            raise ValueError("Invalid Gemini model ID.")
        self._api_key_provider = api_key_provider
        self._client = http_client
        self._model = model
        self._time_budget = time_budget_seconds
        self._max_retries = max_retries

    @property
    def endpoint(self) -> str:
        """The ``generateContent`` URL for the configured model."""
        return f"{GEMINI_API_BASE_URL}/models/{self._model}:generateContent"

    async def _post(self, api_key: str, body: dict) -> httpx.Response:
        return await self._client.post(
            self.endpoint,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
        )

    async def generate(
        self,
        user_id: str,
        target: "WordEntry",
        previous: GeneratedSentence | None,
    ) -> GeneratedSentence:
        """Return the first accepted sentence within the retry budget."""
        # Once per request, before any HTTP request (Req 4.7).
        api_key = self._api_key_provider(user_id)
        if not isinstance(api_key, str) or not api_key.strip():
            raise MissingGeminiKeyError()

        body = build_request_body(target, previous)

        for _attempt in range(self._max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self._post(api_key, body), timeout=self._time_budget
                )
            except TimeoutError:
                # wait_for cancels the in-flight request, so a late response
                # is never read or displayed (Req 4.5).
                raise GenerationTimeout() from None
            except httpx.HTTPError:
                continue  # transport error: retry (Req 4.11)

            if not response.is_success:
                failure = classify_generation_failure(
                    response.status_code, response.content
                )
                if failure is GeminiFailure.QUOTA:
                    raise QuotaExceededError() from None
                if failure is GeminiFailure.INVALID_KEY:
                    raise InvalidGeminiKeyError() from None
                continue  # other 4xx / 5xx: retry (Req 4.11)

            try:
                payload = response.json()
            except ValueError:
                continue
            text = extract_candidate_text(payload)
            if text is None:
                continue
            candidate = parse_sentence(text)
            if candidate is None or not differs_from_previous(candidate, previous):
                continue
            return candidate

        raise GenerationError() from None


__all__ = [
    "DEFAULT_MODEL",
    "GEMINI_API_BASE_URL",
    "GeminiAdapter",
    "MAX_RETRIES",
    "SYSTEM_INSTRUCTION",
    "TIME_BUDGET_SECONDS",
    "build_request_body",
    "build_user_prompt",
    "differs_from_previous",
    "extract_candidate_text",
    "parse_sentence",
]

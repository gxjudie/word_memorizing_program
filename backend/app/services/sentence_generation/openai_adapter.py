"""OpenAI adapter for the Sentence Generation Service.

This module implements the concrete :class:`OpenAIAdapter`, a
:class:`~app.services.sentence_generation.base.SentenceGenerationService` that
produces an Example_Sentence containing the Target_Word by calling the OpenAI
provider for **structured JSON**, re-validating the output with
:class:`~app.schemas.sentence.GeneratedSentenceSchema`, and running the
validate/retry pipeline from the design.

The validate/retry pipeline (design flowchart) enforces, per candidate:

1. **Time budget** - the whole operation must finish within 5 seconds (Req
   4.2); exceeding it raises :class:`GenerationTimeout` (Req 4.5).
2. **Schema validity** - the provider output must parse and satisfy
   :class:`GeneratedSentenceSchema`, which already enforces target presence
   (Req 4.1 / 4.6) and the 1-50 token bound (Req 4.4).
3. **Difference-from-previous** - when a ``previous`` sentence is supplied, the
   candidate must differ from it by at least one non-target word (Req 4.3).

Candidates that fail any check are discarded and generation retries up to 3
times (4 attempts total). If every attempt fails, :class:`GenerationError` is
raised (Req 4.6).

**Security.** The OpenAI API key is a server-held secret: it is read from the
application :class:`~app.config.Settings` and passed to the OpenAI client
inside this adapter only. It is never returned to callers or logged. The
provider call is injected as a plain async callable (``provider_call``) so the
real network client is constructed lazily and so tests can supply a mock
provider — the real API is never contacted from tests.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol

from pydantic import ValidationError

from app.schemas.sentence import GeneratedSentence, GeneratedSentenceSchema
from app.services.sentence_generation.base import (
    GenerationError,
    GenerationTimeout,
    SentenceGenerationService,
)

if TYPE_CHECKING:
    from app.config import Settings
    from app.models.word_entry import WordEntry


# Total wall-clock budget for a single ``generate`` call (Req 4.2).
TIME_BUDGET_SECONDS: float = 5.0

# Number of retries after the first attempt (Req 4.6: "retry up to 3 times").
# This yields up to ``MAX_RETRIES + 1`` provider calls in total.
MAX_RETRIES: int = 3

# Model used for structured generation. Kept here (not in call sites) so the
# provider detail stays inside the adapter.
DEFAULT_MODEL: str = "gpt-4o-mini"


class ProviderCall(Protocol):
    """Injectable provider call signature.

    A ``ProviderCall`` receives a fully-rendered prompt and returns the raw
    JSON **string** the provider produced. Keeping this a narrow async callable
    (rather than the whole OpenAI client) is what makes the adapter trivially
    mockable: a test supplies a function returning canned JSON and the real API
    is never contacted.
    """

    async def __call__(self, prompt: str) -> str:  # pragma: no cover - protocol
        ...


def _build_prompt(target: "WordEntry", previous: GeneratedSentence | None) -> str:
    """Render the instruction prompt for one generation attempt.

    The prompt asks the model for a natural Japanese sentence containing the
    Target_Word (surface or dictionary form), tokenized into 1-50 words with a
    hiragana reading, short meaning, ``is_target`` flag, and ``contains_kanji``
    flag per token. When a ``previous`` sentence is supplied, the prompt
    requires the new sentence to differ by at least one non-target word (Req
    4.3) and includes the previous text for that purpose.

    Args:
        target: The Word_Entry under review.
        previous: The immediately preceding generated sentence, or ``None``.

    Returns:
        The rendered prompt string.
    """
    lines = [
        "You generate Japanese example sentences for a vocabulary trainer.",
        "Return ONLY JSON matching this shape:",
        '{"text": str, "tokens": [{"surface": str, "reading": str, '
        '"meaning": str|null, "is_target": bool, "contains_kanji": bool}]}',
        "Rules:",
        f"- The sentence MUST contain the target word '{target.word_form}' "
        f"(reading '{target.reading}', meaning '{target.meaning}') in surface "
        "or dictionary form.",
        "- Tokenize the sentence into between 1 and 50 word tokens.",
        "- Give each token a hiragana reading and a short meaning.",
        "- Mark the target token(s) with is_target=true; set contains_kanji per token.",
    ]
    if previous is not None:
        lines.append(
            "- The new sentence MUST differ from the previous sentence below by "
            "at least one word other than the target word."
        )
        lines.append(f"Previous sentence: {previous.text}")
    return "\n".join(lines)


def _non_target_words(sentence: GeneratedSentence) -> list[str]:
    """Return the surface forms of all non-target tokens in order.

    Only non-target tokens are considered for the difference-from-previous
    check (Req 4.3): a candidate differing from the previous sentence solely in
    its target token does not count as a new sentence.
    """
    return [t.surface for t in sentence.tokens if not t.is_target]


def _differs_from_previous(
    candidate: GeneratedSentence, previous: GeneratedSentence | None
) -> bool:
    """Return whether ``candidate`` differs from ``previous`` by a non-target word.

    With no previous sentence, any candidate is acceptable. Otherwise the
    candidate is accepted only if its ordered sequence of non-target surface
    forms is not identical to the previous sentence's — i.e. they differ in at
    least one word other than the Target_Word (Req 4.3).

    Args:
        candidate: The freshly generated sentence under consideration.
        previous: The immediately preceding sentence, or ``None`` on first review.

    Returns:
        ``True`` if the candidate is acceptably different; ``False`` otherwise.
    """
    if previous is None:
        return True
    return _non_target_words(candidate) != _non_target_words(previous)


class OpenAIAdapter(SentenceGenerationService):
    """OpenAI-backed :class:`SentenceGenerationService` with validate/retry.

    The adapter never trusts the provider output: every candidate is parsed as
    JSON and re-validated with :class:`GeneratedSentenceSchema` (which enforces
    target presence and the 1-50 token bound), then checked for
    difference-from-previous. It retries up to :data:`MAX_RETRIES` times within
    a :data:`TIME_BUDGET_SECONDS` wall-clock budget.
    """

    def __init__(
        self,
        provider_call: ProviderCall | None = None,
        *,
        settings: "Settings | None" = None,
        model: str = DEFAULT_MODEL,
        time_budget_seconds: float = TIME_BUDGET_SECONDS,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        """Create the adapter.

        Args:
            provider_call: An async callable returning raw JSON for a prompt.
                If omitted, a default call backed by the OpenAI async client is
                built lazily using the server-held key from ``settings``. Tests
                pass a mock here so the real API is never contacted.
            settings: Application settings holding the server-held
                ``openai_api_key``. Defaults to :func:`app.config.get_settings`.
                The key is used only to construct the default provider client
                and is never exposed to callers.
            model: Provider model name for the default provider call.
            time_budget_seconds: Total wall-clock budget for one ``generate``
                call (Req 4.2).
            max_retries: Number of retries after the first attempt (Req 4.6).
        """
        self._model = model
        self._time_budget = time_budget_seconds
        self._max_retries = max_retries
        self._settings = settings
        # The provider call is resolved lazily so importing this module (and
        # constructing the adapter) never requires a configured key.
        self._provider_call = provider_call

    def _get_provider_call(self) -> ProviderCall:
        """Return the provider call, building the default OpenAI one if needed.

        The default call constructs an :class:`openai.AsyncOpenAI` client with
        the server-held key and requests JSON-object output. It is built lazily
        and cached so that constructing the adapter (and importing this module)
        does not require a key or network access.
        """
        if self._provider_call is not None:
            return self._provider_call

        settings = self._settings
        if settings is None:
            from app.config import get_settings

            settings = get_settings()

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        model = self._model

        async def _default_call(prompt: str) -> str:
            response = await client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.choices[0].message.content
            return content or ""

        self._provider_call = _default_call
        return self._provider_call

    def _parse_and_validate(self, raw: str) -> GeneratedSentence | None:
        """Parse raw provider JSON and re-validate it against the schema.

        Returns the validated :class:`GeneratedSentence` on success, or
        ``None`` if the output is not valid JSON or fails schema validation
        (which includes target presence per Req 4.1/4.6 and the 1-50 token
        bound per Req 4.4). A ``None`` return signals the caller to retry.
        """
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        try:
            return GeneratedSentenceSchema.model_validate(payload)
        except ValidationError:
            return None

    async def generate(
        self, target: "WordEntry", previous: GeneratedSentence | None
    ) -> GeneratedSentence:
        """Produce a validated Example_Sentence containing ``target``.

        Runs the validate/retry pipeline inside a 5-second wall-clock budget.
        Each attempt calls the provider, parses and re-validates the JSON,
        enforces target presence (via the schema) and difference-from-previous
        (Req 4.3). The first candidate passing all checks is returned. If the
        budget elapses, :class:`GenerationTimeout` is raised (Req 4.5); if the
        retry budget is exhausted without a valid candidate,
        :class:`GenerationError` is raised (Req 4.6).

        Args:
            target: The Word_Entry under review.
            previous: The immediately preceding generated sentence, or ``None``.

        Returns:
            A validated :class:`GeneratedSentence`.

        Raises:
            GenerationTimeout: If the time budget elapses (Req 4.2, 4.5).
            GenerationError: If all attempts fail (Req 4.1, 4.6).
        """
        prompt = _build_prompt(target, previous)
        provider_call = self._get_provider_call()
        deadline = time.monotonic() + self._time_budget

        try:
            return await asyncio.wait_for(
                self._attempt_loop(prompt, previous, provider_call, deadline),
                timeout=self._time_budget,
            )
        except asyncio.TimeoutError as exc:
            raise GenerationTimeout(
                "Sentence generation exceeded the 5s time budget"
            ) from exc

    async def _attempt_loop(
        self,
        prompt: str,
        previous: GeneratedSentence | None,
        provider_call: ProviderCall,
        deadline: float,
    ) -> GeneratedSentence:
        """Run up to ``max_retries + 1`` attempts, honoring the deadline.

        Each iteration enforces the remaining time budget before and after the
        provider call so a slow provider cannot blow past the 5s limit even
        between retries. Returns the first fully-valid candidate; raises
        :class:`GenerationError` if none pass within the retry budget.
        """
        for attempt in range(self._max_retries + 1):
            if time.monotonic() >= deadline:
                raise GenerationTimeout(
                    "Sentence generation exceeded the 5s time budget"
                )

            raw = await provider_call(prompt)

            if time.monotonic() >= deadline:
                raise GenerationTimeout(
                    "Sentence generation exceeded the 5s time budget"
                )

            candidate = self._parse_and_validate(raw)
            if candidate is None:
                continue  # invalid JSON / schema / missing target -> retry
            if not _differs_from_previous(candidate, previous):
                continue  # too similar to previous -> retry
            return candidate

        raise GenerationError(
            f"Failed to generate a valid sentence after "
            f"{self._max_retries + 1} attempts"
        )


__all__ = ["OpenAIAdapter"]

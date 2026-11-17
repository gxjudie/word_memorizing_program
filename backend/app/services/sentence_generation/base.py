"""Provider-agnostic sentence-generation contract and failure types.

Sentence generation uses each Learner's own Gemini_API_Key (BYOK). Concrete
adapters receive an :class:`ApiKeyProvider` and call it with the ``user_id``
passed to :meth:`SentenceGenerationService.generate` once per generation
request. There is no developer-held key (Requirement 10.16).

Every exception defined here carries a fixed, key-free message by default
(Requirement 10.13). Callers must never build a message from provider output
or from the key itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Protocol

if TYPE_CHECKING:
    from app.models.word_entry import WordEntry
    from app.schemas.sentence import GeneratedSentence


class SentenceGenerationError(Exception):
    """Base class for generation failures. Messages never contain a key.

    Each subclass defines a fixed ``default_message`` that is used when the
    exception is raised without arguments.
    """

    default_message: ClassVar[str] = "Sentence generation failed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message if message is not None else self.default_message)


class MissingGeminiKeyError(SentenceGenerationError):
    """The Learner has no stored Gemini_API_Key; no Gemini request is sent.

    Requirements 4.7 and 10.15. The Target_Word stays in the session.
    """

    default_message: ClassVar[str] = "No Gemini API key is connected."


class InvalidGeminiKeyError(SentenceGenerationError):
    """Gemini rejected the key (400 API_KEY_INVALID, 401, 403 PERMISSION_DENIED).

    Requirement 4.9. The request stops with no further attempts.
    """

    default_message: ClassVar[str] = "The Gemini API key was rejected."


class QuotaExceededError(SentenceGenerationError):
    """Gemini returned 429 / RESOURCE_EXHAUSTED for the Learner's key.

    Requirement 4.8. The request stops with no further attempts.
    """

    default_message: ClassVar[str] = "The Gemini API quota is exhausted."


class GenerationTimeout(SentenceGenerationError):
    """An attempt exceeded its five-second time budget (Requirements 4.2, 4.5)."""

    default_message: ClassVar[str] = "Sentence generation timed out."


class GenerationError(SentenceGenerationError):
    """The retry budget was exhausted by discarded outputs or transient errors.

    Requirements 4.6, 4.10, and 4.11.
    """

    default_message: ClassVar[str] = "Could not generate a valid sentence."


class ApiKeyProvider(Protocol):
    """Returns the Learner's decrypted Gemini_API_Key for server-side use only.

    Bound to ``GeminiKeyService.get_api_key`` and injected into the Gemini
    adapter. Raises :class:`MissingGeminiKeyError` when no key is stored.
    """

    def __call__(self, user_id: str) -> str:
        """Return the stored key for ``user_id``."""
        ...


class SentenceGenerationService(ABC):
    """Provider-agnostic interface for producing an Example_Sentence."""

    @abstractmethod
    async def generate(
        self,
        user_id: str,
        target: "WordEntry",
        previous: "GeneratedSentence | None",
    ) -> "GeneratedSentence":
        """Produce a validated Example_Sentence containing ``target``.

        Args:
            user_id: The signed-in Learner whose key the injected
                :class:`ApiKeyProvider` supplies (once per request).
            target: The Word_Entry under review (Requirement 4.1).
            previous: The immediately preceding generated sentence for this
                word, or ``None`` on its first review (Requirement 4.3).

        Returns:
            A validated Example_Sentence containing the Target_Word.

        Raises:
            MissingGeminiKeyError: No key is stored (Requirement 4.7).
            QuotaExceededError: Gemini quota exhausted (Requirement 4.8).
            InvalidGeminiKeyError: Gemini rejected the key (Requirement 4.9).
            GenerationTimeout: An attempt exceeded five seconds
                (Requirements 4.2, 4.5).
            GenerationError: The retry budget was exhausted
                (Requirements 4.10, 4.11).
        """
        ...


__all__ = [
    "ApiKeyProvider",
    "GenerationError",
    "GenerationTimeout",
    "InvalidGeminiKeyError",
    "MissingGeminiKeyError",
    "QuotaExceededError",
    "SentenceGenerationError",
    "SentenceGenerationService",
]

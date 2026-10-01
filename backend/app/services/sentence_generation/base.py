"""Sentence Generation Service interface and error types.

This module defines the provider-agnostic contract for producing an
Example_Sentence that contains a Target_Word (Req 4). Concrete adapters (e.g.
the OpenAI adapter in ``openai_adapter.py``) implement
:class:`SentenceGenerationService`; the backend holds the provider key
server-side and proxies calls on the Learner's behalf.

The two exception types model the two terminal failure modes of the
validate/retry pipeline described in the design:

* :class:`GenerationTimeout` - the provider did not return a sentence within
  the time budget (Req 4.2, 4.5).
* :class:`GenerationError` - generation exhausted its retry budget without
  producing a valid sentence that contains the Target_Word (Req 4.1, 4.6).

Both are mapped by the frontend to the generation-error message, which retains
the Target_Word in the session and offers a retry control (Req 4.5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.word_entry import WordEntry
    from app.schemas.sentence import GeneratedSentence


class SentenceGenerationError(Exception):
    """Base class for all sentence-generation failures.

    Catching this base type lets callers handle any generation failure
    uniformly while still being able to distinguish the specific subclasses
    (:class:`GenerationTimeout` vs :class:`GenerationError`) when needed.
    """


class GenerationTimeout(SentenceGenerationError):
    """Raised when generation does not complete within the time budget.

    Satisfies Req 4.2 / 4.5: the provider call must produce an Example_Sentence
    within 5 seconds; otherwise the System surfaces a generation-error message,
    retains the Target_Word in the current Review_Session, and offers a retry
    control.
    """


class GenerationError(SentenceGenerationError):
    """Raised when generation fails to produce a valid sentence.

    Satisfies Req 4.1 / 4.6: if the provider cannot produce a schema-valid
    Example_Sentence that contains the Target_Word within the retry budget (up
    to 3 retries), generation gives up and raises this error.
    """


class SentenceGenerationService(ABC):
    """Provider-agnostic interface for generating an Example_Sentence.

    Implementations encapsulate the provider call, structured-output parsing,
    schema re-validation, target-presence enforcement, difference-from-previous
    enforcement, and the retry/timeout policy. Keeping this an abstract base
    class lets the provider be swapped and lets tests inject a mock without a
    real network call.
    """

    @abstractmethod
    async def generate(
        self, target: "WordEntry", previous: "GeneratedSentence | None"
    ) -> "GeneratedSentence":
        """Produce an Example_Sentence containing ``target``.

        Args:
            target: The Word_Entry under review; the produced sentence must
                contain its word form in surface or dictionary form (Req 4.1).
            previous: The immediately preceding generated sentence for this
                word, or ``None`` on the first review. When provided, the new
                sentence must differ from it by at least one non-target word
                (Req 4.3).

        Returns:
            A validated :class:`GeneratedSentence` with 1-50 word tokens
            (Req 4.4) that contains the Target_Word.

        Raises:
            GenerationTimeout: If generation does not complete within the time
                budget (Req 4.2, 4.5).
            GenerationError: If generation exhausts its retry budget without
                producing a valid, target-containing sentence (Req 4.1, 4.6).

        The method is ``async`` to suit the slow provider call without blocking
        the event loop.
        """
        ...

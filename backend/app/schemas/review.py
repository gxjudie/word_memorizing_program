"""Request/response schemas for Review_Sessions (Req 6, 7, 8).

These model the review loop boundary: starting a session returns either a
``ReviewSessionOut`` (the due words to review) or a ``NoDueWords`` marker when
nothing is due (Req 8.1, 8.5). Recording a rating takes a ``RatingInput`` and
yields a ``RatingResult`` that either advances to the next Due_Word or carries
the ``SessionSummary`` when the final word has been rated (Req 8.3, 8.4).

Answer secrecy at the API boundary
----------------------------------
Context-first recall only works if the answer is not already in the browser.
Starting a session therefore returns ``DueWordOut`` — id and word form only —
and the reading and meaning arrive from the reveal route as
``RevealedWordOut`` immediately before the Learner may rate. The client never
receives the answer of a word it has not revealed.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.difficulty import Difficulty


class DueWordOut(BaseModel):
    """A Due_Word shown before its answer is revealed.

    Deliberately excludes ``reading``, ``meaning``, and scheduling state: the
    review payload must not carry the answer (see the module docstring).

    Attributes:
        id: The Word_Entry id.
        word_form: The Japanese word form, which the learner is recalling.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    word_form: str


class RevealedWordOut(BaseModel):
    """The answer of the current Target_Word, returned by the reveal route.

    Attributes:
        id: The Word_Entry id.
        word_form: The Japanese word form.
        reading: The kana reading.
        meaning: The Learner's gloss/meaning.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    word_form: str
    reading: str
    meaning: str


class ReviewSessionOut(BaseModel):
    """A started Review_Session with its selected Due_Words (Req 8.1, 8.2).

    Attributes:
        session_id: Identifier of the created session.
        due_words: The Word_Entries due on or before the session date, in the
            order they will be presented one at a time, without their answers.
    """

    session_id: str
    due_words: list[DueWordOut]


class NoDueWords(BaseModel):
    """Marker returned when no Word_Entries are due (Req 8.5).

    No session is created in this case; the ``message`` is shown to the Learner.
    The constant ``no_due_words`` discriminator lets the client distinguish this
    from a ``ReviewSessionOut`` without inspecting field presence.
    """

    status: str = "no_due_words"
    message: str = "No words are due for review today."


class RatingInput(BaseModel):
    """Record-rating request body (Req 6.4, 8.6).

    ``rating`` is validated against the four defined ``Difficulty`` levels, so a
    value outside {1, 2, 3, 4} is rejected at the schema boundary (Req 8.6).
    """

    rating: Difficulty


class SessionSummary(BaseModel):
    """End-of-session summary (Req 8.4).

    Attributes:
        total_reviewed: Total count of Due_Words reviewed in the session.
        counts_by_rating: Tally of Difficulty_Ratings recorded, keyed by level.
    """

    total_reviewed: int
    counts_by_rating: dict[Difficulty, int] = Field(default_factory=dict)


class RatingResult(BaseModel):
    """Outcome of recording a rating (Req 7.4, 8.3, 8.4).

    After a successful rating the Scheduler's computed ``next_review_date`` is
    returned. If more Due_Words remain, ``next_word`` carries the next word to
    present (Req 8.3); when the final word has been rated, ``summary`` carries
    the session summary and ``session_complete`` is ``True`` (Req 8.4).
    """

    model_config = ConfigDict(from_attributes=True)

    word_id: str
    next_review_date: date
    session_complete: bool = False
    next_word: DueWordOut | None = None
    summary: SessionSummary | None = None

class SentenceGenerationErrorDetail(BaseModel):
    """Stable, client-safe ``{code, message}`` for review-route Gemini failures.

    ``gemini_key_required`` is also returned by session start (Req 10.1).
    Messages are fixed strings and never contain a Gemini_API_Key.
    """
    code: Literal[
        "gemini_key_required",
        "invalid_gemini_key",
        "gemini_quota_exceeded",
        "generation_timeout",
        "generation_failed",
    ]
    message: str


class SentenceGenerationErrorResponse(BaseModel):
    """FastAPI error envelope for typed generation failures."""
    detail: SentenceGenerationErrorDetail

"""Request/response schemas for Review_Sessions (Req 6, 7, 8).

These model the review loop boundary: starting a session returns either a
``ReviewSessionOut`` (the due words to review) or a ``NoDueWords`` marker when
nothing is due (Req 8.1, 8.5). Recording a rating takes a ``RatingInput`` and
yields a ``RatingResult`` that either advances to the next Due_Word or carries
the ``SessionSummary`` when the final word has been rated (Req 8.3, 8.4).
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.difficulty import Difficulty
from app.schemas.word import WordEntryOut


class ReviewSessionOut(BaseModel):
    """A started Review_Session with its selected Due_Words (Req 8.1, 8.2).

    Attributes:
        session_id: Identifier of the created session.
        due_words: The Word_Entries due on or before the session date, in the
            order they will be presented one at a time.
    """

    session_id: str
    due_words: list[WordEntryOut]


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
    next_word: WordEntryOut | None = None
    summary: SessionSummary | None = None

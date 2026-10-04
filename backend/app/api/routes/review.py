"""Review session HTTP routes (Req 3, 4, 6, 7, 8).

This router is the HTTP seam over :class:`~app.services.review_service.ReviewService`
and the :class:`~app.services.sentence_generation.base.SentenceGenerationService`.
It holds only HTTP concerns — routing, (de)serialization, status-code mapping —
and delegates all business rules to the services (see the layer separation in
the design). Every route requires an authenticated Learner.

Endpoints (see the design API table):

* ``POST /api/review/sessions`` — start a session over the Learner's Due_Words,
  returning the due list or a no-due marker (Req 8.1, 8.5).
* ``POST /api/review/sessions/{id}/words/{wordId}/reveal`` — mark the current
  Target_Word revealed; the reveal-before-rate gate (Req 6.5).
* ``POST /api/review/sessions/{id}/words/{wordId}/sentence`` — proxy a fresh
  Example_Sentence from the LLM, re-validated server-side, persisting it and
  returning it; generation timeout/exhaustion map to a generation-error
  response so the client can retain the word and offer retry (Req 4.5, 4.6).
* ``POST /api/review/sessions/{id}/words/{wordId}/rating`` — record a rating,
  schedule, persist transactionally, and advance or summarize (Req 6, 7, 8).

The LLM key is held server-side by the sentence service and never reaches the
client.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, get_review_service, get_sentence_service
from app.models.generated_sentence import GeneratedSentence as GeneratedSentenceORM
from app.models.word_entry import WordEntry
from app.schemas.review import NoDueWords, RatingInput, RatingResult, ReviewSessionOut
from app.schemas.sentence import GeneratedSentence as GeneratedSentenceSchema
from app.services.review_service import (
    InvalidRatingError,
    NotRevealedError,
    ReviewService,
    SchedulingNotSavedError,
    SessionNotFoundError,
    WordNotCurrentError,
)
from app.services.sentence_generation.base import (
    GenerationError,
    GenerationTimeout,
    SentenceGenerationService,
)

__all__ = ["router"]

router = APIRouter()


class RevealResponse(BaseModel):
    """Acknowledges that the current Target_Word has been revealed."""

    revealed: bool = True


@router.post(
    "/sessions",
    response_model=ReviewSessionOut | NoDueWords,
    summary="Start a review session",
)
def start_session(
    user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> ReviewSessionOut | NoDueWords:
    """Start a Review_Session over the Learner's Due_Words (Req 8.1, 8.5).

    Returns the due words to review, or a :class:`NoDueWords` marker (still
    ``200``) when nothing is due so the client can show the "no words due"
    state.
    """
    return service.start_session(user.id, date.today())


@router.post(
    "/sessions/{session_id}/words/{word_id}/reveal",
    response_model=RevealResponse,
    summary="Reveal the current word's answer",
)
def reveal(
    session_id: str,
    word_id: str,
    user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> RevealResponse:
    """Mark the current Target_Word's answer revealed (Req 6.5 gate).

    Raises:
        HTTPException: ``404`` if the session is unknown; ``409`` if the word is
            not the one currently presented.
    """
    try:
        service.reveal(session_id, word_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown review session"
        ) from exc
    except WordNotCurrentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Word is not the current word in this session",
        ) from exc
    return RevealResponse()


def _latest_previous_sentence(
    db: Session, word_id: str
) -> GeneratedSentenceSchema | None:
    """Return the most recent generated sentence for a word as the schema model.

    Used as the ``previous`` argument to the generator so the new sentence must
    differ from it by a non-target word (Req 4.3). Returns ``None`` on the first
    review (no prior sentence).
    """
    row = db.execute(
        select(GeneratedSentenceORM)
        .where(GeneratedSentenceORM.word_id == word_id)
        .order_by(GeneratedSentenceORM.created_at.desc(), GeneratedSentenceORM.id.desc())
    ).scalars().first()
    if row is None:
        return None
    return GeneratedSentenceSchema.model_validate({"text": row.text, "tokens": row.tokens})


@router.post(
    "/sessions/{session_id}/words/{word_id}/sentence",
    response_model=GeneratedSentenceSchema,
    summary="Generate an example sentence for the current word",
)
async def generate_sentence(
    session_id: str,
    word_id: str,
    user: CurrentUser,
    db: DbSession,
    service: ReviewService = Depends(get_review_service),
    sentence_service: SentenceGenerationService = Depends(get_sentence_service),
) -> GeneratedSentenceSchema:
    """Proxy a fresh Example_Sentence for the current word (Req 3, 4).

    Loads the word (scoped to the Learner), supplies the immediately-preceding
    sentence for the difference check (Req 4.3), calls the LLM proxy, persists
    the validated result, and returns it. A generation timeout or exhausted
    retry budget maps to a ``502`` generation-error response so the client
    retains the word and offers a retry control (Req 4.5, 4.6).

    Raises:
        HTTPException: ``404`` if the session or word is unknown/not owned;
            ``502`` with a stable error code on generation timeout/failure.
    """
    # Confirm the session exists (surface a 404 rather than a vague error).
    try:
        service._get_session(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown review session"
        ) from exc

    word = db.execute(
        select(WordEntry).where(
            WordEntry.id == word_id, WordEntry.user_id == user.id
        )
    ).scalar_one_or_none()
    if word is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown word"
        )

    previous = _latest_previous_sentence(db, word_id)

    try:
        generated = await sentence_service.generate(word, previous)
    except GenerationTimeout as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "generation_timeout",
                "message": "Sentence generation timed out. Please retry.",
            },
        ) from exc
    except GenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "generation_failed",
                "message": "Could not generate a sentence. Please retry.",
            },
        ) from exc

    # Persist the validated sentence (tokens stored as JSON).
    row = GeneratedSentenceORM(
        word_id=word.id,
        text=generated.text,
        tokens=[token.model_dump() for token in generated.tokens],
    )
    db.add(row)
    db.commit()

    return generated


@router.post(
    "/sessions/{session_id}/words/{word_id}/rating",
    response_model=RatingResult,
    summary="Record a difficulty rating for the current word",
)
def record_rating(
    session_id: str,
    word_id: str,
    body: RatingInput,
    user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> RatingResult:
    """Record a Difficulty_Rating, schedule, persist, and advance (Req 6, 7, 8).

    Raises:
        HTTPException: ``404`` unknown session; ``409`` word not current or not
            revealed (Req 6.5); ``400`` out-of-set rating (Req 8.6); ``500``
            when the scheduling write fails (Req 7.5).
    """
    try:
        return service.record_rating(user.id, session_id, word_id, body.rating)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown review session"
        ) from exc
    except NotRevealedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The word must be revealed before rating",
        ) from exc
    except WordNotCurrentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Word is not the current word in this session",
        ) from exc
    except InvalidRatingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rating must be one of 1, 2, 3, 4",
        ) from exc
    except SchedulingNotSavedError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Scheduling not saved; please try again",
        ) from exc

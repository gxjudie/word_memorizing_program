"""Review session HTTP routes (Req 3, 4, 6, 7, 8).

This router is the HTTP seam over :class:`~app.services.review_service.ReviewService`
and the :class:`~app.services.sentence_generation.base.SentenceGenerationService`.
It holds only HTTP concerns — routing, (de)serialization, status-code mapping —
and delegates all business rules to the services (see the layer separation in
the design). Every route requires an authenticated Learner.

Endpoints (see the design API table):

* ``POST /api/review/sessions`` — ``409 gemini_key_required`` when the Learner
  has no stored Gemini_API_Key (checked first, Req 10.1); otherwise start a
  session over the Learner's Due_Words, returning the due list or a no-due
  marker (Req 8.1, 8.5).
* ``POST /api/review/sessions/{id}/words/{wordId}/reveal`` — mark the current
  Target_Word revealed; the reveal-before-rate gate (Req 6.5).
* ``POST /api/review/sessions/{id}/words/{wordId}/sentence`` — generate a fresh
  Example_Sentence through Gemini with the Learner's own key, re-validated
  server-side, persist it, and return it. Gemini BYOK failures map to typed
  ``{code, message}`` errors (see ``_GENERATION_ERRORS``); the Target_Word
  stays unrated in the session (Req 4.5, 4.7-4.11).
* ``POST /api/review/sessions/{id}/words/{wordId}/rating`` — record a rating,
  schedule, persist transactionally, and advance or summarize (Req 6, 7, 8).

The Learner's Gemini_API_Key is decrypted and attached server-side by the
sentence service and never reaches the client (Req 10.12, 10.16).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, get_review_service, get_sentence_service
from app.models.generated_sentence import GeneratedSentence as GeneratedSentenceORM
from app.models.word_entry import WordEntry
from app.schemas.review import (
    NoDueWords,
    RatingInput,
    RatingResult,
    ReviewSessionOut,
    SentenceGenerationErrorDetail,
    SentenceGenerationErrorResponse,
)
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
    InvalidGeminiKeyError,
    MissingGeminiKeyError,
    QuotaExceededError,
    SentenceGenerationError,
    SentenceGenerationService,
)
from app.services.study_day import current_study_day

__all__ = ["router"]

router = APIRouter()


# Gemini BYOK failure -> (HTTP status, code, fixed key-free message). The
# message is never built from the exception or provider output (Req 10.13).
_GENERATION_ERRORS: dict[type[SentenceGenerationError], tuple[int, str, str]] = {
    MissingGeminiKeyError: (
        status.HTTP_409_CONFLICT,
        "gemini_key_required",
        "Set up a Gemini API key to generate example sentences.",
    ),
    InvalidGeminiKeyError: (
        422,
        "invalid_gemini_key",
        "Your Gemini API key is invalid or revoked. Replace it to continue.",
    ),
    QuotaExceededError: (
        status.HTTP_429_TOO_MANY_REQUESTS,
        "gemini_quota_exceeded",
        "Your Gemini API quota is exhausted. Wait and retry later.",
    ),
    GenerationTimeout: (
        status.HTTP_502_BAD_GATEWAY,
        "generation_timeout",
        "Sentence generation timed out. Please retry.",
    ),
    GenerationError: (
        status.HTTP_502_BAD_GATEWAY,
        "generation_failed",
        "Could not generate a sentence. Please retry.",
    ),
}


def _generation_http_error(exc: SentenceGenerationError) -> HTTPException:
    """Map a generation failure to its typed ``{code, message}`` HTTP error.

    An unmapped ``SentenceGenerationError`` subclass falls back to
    ``generation_failed`` so no provider detail can reach the client.
    """
    for exc_type, (status_code, code, message) in _GENERATION_ERRORS.items():
        if isinstance(exc, exc_type):
            break
    else:
        status_code, code, message = _GENERATION_ERRORS[GenerationError]
    detail = SentenceGenerationErrorDetail(code=code, message=message)
    return HTTPException(status_code=status_code, detail=detail.model_dump())


class RevealResponse(BaseModel):
    """Acknowledges that the current Target_Word has been revealed."""

    revealed: bool = True


@router.post(
    "/sessions",
    response_model=ReviewSessionOut | NoDueWords,
    responses={
        status.HTTP_409_CONFLICT: {
            "model": SentenceGenerationErrorResponse,
            "description": "No Gemini API key is stored (gemini_key_required)",
        },
    },
    summary="Start a review session",
)
def start_session(
    user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> ReviewSessionOut | NoDueWords:
    """Start a Review_Session over the Learner's Due_Words (Req 10.1, 8.1, 8.5).

    Without a stored Gemini_API_Key the session is not started and the route
    returns ``409 gemini_key_required`` so the client opens the
    Key_Setup_Guide (Req 10.1). Otherwise it returns the due words to review,
    or a :class:`NoDueWords` marker (still ``200``) when nothing is due.
    """
    try:
        return service.start_session(user.id, current_study_day())
    except MissingGeminiKeyError as exc:
        raise _generation_http_error(exc) from None


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
    responses={
        status.HTTP_409_CONFLICT: {
            "model": SentenceGenerationErrorResponse,
            "description": "No Gemini API key is stored (gemini_key_required)",
        },
        422: {
            "model": SentenceGenerationErrorResponse,
            "description": "Gemini rejected the stored key (invalid_gemini_key)",
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "model": SentenceGenerationErrorResponse,
            "description": "Gemini quota exhausted (gemini_quota_exceeded)",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "model": SentenceGenerationErrorResponse,
            "description": "Sentence generation failed or timed out",
        },
    },
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
    """Generate a fresh Example_Sentence for the current word (Req 3, 4).

    Loads the word (scoped to the Learner), supplies the immediately-preceding
    sentence for the difference check (Req 4.3), calls Gemini with the
    Learner's own key, persists the validated result, and returns it.

    Raises:
        HTTPException: ``404`` if the session or word is unknown/not owned;
            otherwise a ``{code, message}`` error: ``409 gemini_key_required``
            (4.7), ``422 invalid_gemini_key`` (4.9), ``429
            gemini_quota_exceeded`` (4.8), ``502 generation_timeout`` (4.5),
            or ``502 generation_failed`` (4.10, 4.11). Nothing is persisted
            and the Target_Word stays current and unrated.
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
        generated = await sentence_service.generate(user.id, word, previous)
    except SentenceGenerationError as exc:
        # The session cursor is untouched, so the word stays current/unrated.
        raise _generation_http_error(exc) from None

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

"""Word_Collection HTTP routes (Req 1).

This router is the HTTP layer for a Learner's Word_Collection; it holds no
business rules and simply wires requests to :class:`WordService` (see the
layer separation in the design). Every route requires an authenticated
Learner via the :data:`~app.api.deps.CurrentUser` dependency.

Endpoints (see the design API table):

* ``POST /api/words`` — add a word. The request body is validated against
  :class:`~app.schemas.word.WordInput`; a submission with missing /
  whitespace-only / over-length fields is rejected with **400** whose detail
  names *exactly* the offending fields (Req 1.2, 1.3, Property 2). A word form
  that already exists in the Learner's collection is rejected with **409**
  (Req 1.4). On success the stored record is returned (Req 1.1, 1.5).
* ``GET /api/words`` — list the Learner's Word_Collection (supports the review
  flow).

The add route validates the body *inside* the handler (rather than binding
``WordInput`` as the parameter directly) so that Pydantic validation failures
surface as a **400** with field-exact detail, matching the design's documented
status code, instead of FastAPI's default **422**.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import ValidationError

from app.api.deps import CurrentUser, get_word_service
from app.schemas.word import WordEntryOut, WordInput
from app.services.word_service import DuplicateError, WordService

router = APIRouter()

WordServiceDep = Annotated[WordService, Depends(get_word_service)]


def _field_errors(exc: ValidationError) -> list[dict[str, str]]:
    """Flatten a Pydantic ``ValidationError`` into per-field error entries.

    Each entry names one offending top-level field and its message, so a body
    with several bad fields reports *each* of them (Property 2). ``loc`` holds
    the field path; the first element is the top-level field name.
    """
    errors: list[dict[str, str]] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        field = str(loc[0]) if loc else "body"
        errors.append({"field": field, "message": err.get("msg", "invalid")})
    return errors


@router.post(
    "",
    response_model=WordEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a word to the collection",
)
def add_word(
    user: CurrentUser,
    service: WordServiceDep,
    body: Annotated[dict[str, Any], Body(...)],
) -> WordEntryOut:
    """Add a word (validate, dedupe, due-today) — Req 1.1-1.5.

    Args:
        user: The authenticated Learner (owner of the new entry).
        service: The word service bound to the request DB session.
        body: The raw add-word JSON body, validated here against
            :class:`WordInput`.

    Returns:
        The persisted word as a :class:`WordEntryOut` (``201 Created``).

    Raises:
        HTTPException: ``400`` naming each offending field when validation
            fails (Req 1.2, 1.3); ``409`` when the word form already exists in
            the Learner's collection (Req 1.4).
    """
    try:
        data = WordInput.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid word input", "errors": _field_errors(exc)},
        ) from exc

    try:
        entry = service.add_word(user.id, data)
    except DuplicateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Word form already exists: {exc.word_form}",
        ) from exc

    return WordEntryOut.model_validate(entry)


@router.get(
    "",
    response_model=list[WordEntryOut],
    summary="List the Word_Collection",
)
def list_words(
    user: CurrentUser,
    service: WordServiceDep,
) -> list[WordEntryOut]:
    """Return the authenticated Learner's Word_Collection.

    Args:
        user: The authenticated Learner.
        service: The word service bound to the request DB session.

    Returns:
        The Learner's words as :class:`WordEntryOut` records (oldest first);
        an empty list when the collection is empty.
    """
    entries = service.list_collection(user.id)
    return [WordEntryOut.model_validate(entry) for entry in entries]

"""Study_Settings HTTP routes (Req 11.3-11.5, 11.8, 11.9).

HTTP layer only: storage lives in :class:`StudySettingsService`. Both routes
depend on ``CurrentUser``, so a missing, invalid, expired, or revoked
App_Session gets the standard ``401`` before the body is read, and no settings
are returned or changed (Req 11.9). Each handler passes ``user.id``, never an
id from the request, so a Learner reaches only their own row (Req 11.8). No
Gemini key is required.

Endpoints (design API table):

* ``GET /api/study-settings`` -> ``200 {new_word_limit, review_word_limit}``
* ``PUT /api/study-settings`` with ``{new_word_limit, review_word_limit}`` ->
  ``200 {new_word_limit, review_word_limit}`` (stored values) or
  ``422 {"detail": {"code": "invalid_study_settings", "message", "errors"}}``
  with nothing stored.

The PUT body is parsed by hand so every invalid submission gets the coded
shape, never FastAPI's list-shaped ``422`` (the SPA's ``classify()`` would
report that as ``invalid_rating``). Messages are fixed strings and never echo
the submitted values.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from app.api.deps import CurrentUser, StudySettingsServiceDep
from app.schemas.study_settings import StudySettingsIn, StudySettingsOut

__all__ = ["router", "INVALID_STUDY_SETTINGS_CODE"]

router = APIRouter()

INVALID_STUDY_SETTINGS_CODE = "invalid_study_settings"
INVALID_STUDY_SETTINGS_MESSAGE = "Study limits must be whole numbers from 0 to 1000."
INVALID_FIELD_MESSAGE = "Must be a whole number from 0 to 1000."

# Fixed field order for the ``errors`` list.
_FIELDS: tuple[str, ...] = ("new_word_limit", "review_word_limit")


def _invalid(fields: set[str]) -> HTTPException:
    """Build the coded ``422`` naming each invalid field, in ``_FIELDS`` order."""
    return HTTPException(
        status_code=422,
        detail={
            "code": INVALID_STUDY_SETTINGS_CODE,
            "message": INVALID_STUDY_SETTINGS_MESSAGE,
            "errors": [
                {"field": field, "message": INVALID_FIELD_MESSAGE}
                for field in _FIELDS
                if field in fields
            ],
        },
    )


def parse_study_settings_body(raw: bytes) -> StudySettingsIn:
    """Parse and validate the PUT body, or raise the coded ``422`` (Req 11.5).

    Empty or malformed JSON and a non-object body name both fields. A
    ``ValidationError`` names each invalid field once, keyed by ``loc[0]``.
    """
    try:
        payload: Any = json.loads(raw)
    except (ValueError, RecursionError):  # includes UnicodeDecodeError
        payload = None
    if not isinstance(payload, dict):
        raise _invalid(set(_FIELDS))

    invalid: set[str] = set()
    try:
        return StudySettingsIn.model_validate(payload)
    except ValidationError as exc:
        for error in exc.errors():
            loc = error.get("loc") or ()
            if loc and loc[0] in _FIELDS:
                invalid.add(str(loc[0]))
    # Raised outside the ``except`` block so no exception context is chained.
    raise _invalid(invalid or set(_FIELDS))


@router.get("", response_model=StudySettingsOut, summary="Get the Learner's study limits")
def get_study_settings(
    user: CurrentUser, service: StudySettingsServiceDep
) -> StudySettingsOut:
    """Return the Learner's stored limits, 50 / 100 until saved (Req 11.2, 11.3)."""
    return service.get(user.id)


@router.put("", response_model=StudySettingsOut, summary="Save the Learner's study limits")
async def put_study_settings(
    request: Request, user: CurrentUser, service: StudySettingsServiceDep
) -> StudySettingsOut:
    """Validate and store both limits together (Req 11.4, 11.5).

    Nothing is stored on a rejected submission, so both previous values stay.
    """
    data = parse_study_settings_body(await request.body())
    return await run_in_threadpool(service.update, user.id, data)

"""Request/response schemas for ``/api/study-settings`` (Requirement 11).

``StudySettingsIn`` is the only way a value reaches
:meth:`app.services.study_settings_service.StudySettingsService.update`, so the
0..1000 range is enforced here at the API boundary (Req 11.4, 11.5).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.models.user import STUDY_LIMIT_MAX, STUDY_LIMIT_MIN

__all__ = ["StudyLimit", "StudySettingsIn", "StudySettingsOut"]

StudyLimit = Annotated[StrictInt, Field(ge=STUDY_LIMIT_MIN, le=STUDY_LIMIT_MAX)]


class StudySettingsIn(BaseModel):
    """PUT body (Req 11.4, 11.5). StrictInt rejects bool, float (also 5.0),
    numeric strings, and null; both fields are required."""

    new_word_limit: StudyLimit
    review_word_limit: StudyLimit


class StudySettingsOut(BaseModel):
    """GET / PUT response: the Learner's stored limits (Req 11.3)."""

    model_config = ConfigDict(from_attributes=True)

    new_word_limit: int
    review_word_limit: int

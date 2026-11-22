"""Study_Day helper: the study date of an instant (Req 1.5, 2.26, 8.1).

A Study_Day starts at 07:00 Asia/Seoul time (KST, UTC+9, no DST), not at the
server's local midnight. ``study_day`` is pure; ``current_study_day`` applies it
to the current UTC instant. Every place that needs "today" (due selection,
manual add, Anki import) calls ``current_study_day()``.

``ZoneInfo("Asia/Seoul")`` is resolved at import time. The ``tzdata``
dependency supplies the zone on hosts without a system time zone database;
without either, the import raises ``ZoneInfoNotFoundError`` so the app fails
at startup instead of silently using the server's local date.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

__all__ = ["STUDY_DAY_START", "STUDY_DAY_ZONE", "current_study_day", "study_day"]

STUDY_DAY_ZONE = ZoneInfo("Asia/Seoul")  # KST, UTC+9, no DST
STUDY_DAY_START = time(7, 0)  # a Study_Day starts at 07:00 KST
_START_OFFSET = timedelta(hours=STUDY_DAY_START.hour, minutes=STUDY_DAY_START.minute)


def _utc_now() -> datetime:
    """The current instant as an aware UTC datetime."""
    return datetime.now(UTC)


def study_day(now_utc: datetime) -> date:
    """The Study_Day of an instant (glossary: Study_Day).

    A naive datetime is treated as UTC, because the app stores and compares
    UTC instants (SQLite ``CURRENT_TIMESTAMP`` and ``datetime.now(UTC)`` are
    both UTC). An aware datetime in any zone is converted to KST first. The
    result is the KST date when the KST wall time is 07:00 or later, else the
    previous date. Equivalently, for KST's fixed offset:
    ``(now_utc in UTC + 2 h).date()``.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    local = now_utc.astimezone(STUDY_DAY_ZONE)
    return (local - _START_OFFSET).date()  # wall-clock arithmetic


def current_study_day(clock: Callable[[], datetime] | None = None) -> date:
    """``study_day(clock())``, defaulting to the current UTC instant.

    The clock is injectable for tests. ``_utc_now`` is looked up at call time,
    not bound as a default, so tests can also monkeypatch
    ``app.services.study_day._utc_now``.
    """
    return study_day((clock or _utc_now)())

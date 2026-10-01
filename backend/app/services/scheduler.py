"""Pure spaced-repetition scheduler (SM-2 / Anki-style).

This module implements the Scheduler's single responsibility: computing the next
review interval, in whole days, from a Learner's Difficulty_Rating and a word's
review history. It is a **pure, side-effect-free function** (Req 7) — it reads
no clock, touches no database, and mutates nothing. Persisting the computed
interval and the next review date is a separate concern handled by the Review
Service.

Algorithm (see design.md "Spaced-Repetition Algorithm"):

* **No prior history (Req 7.2):** the interval depends *only* on the rating, via
  a fixed initial table ``L1->1, L2->1, L3->1, L4->4`` days — independent of any
  (absent) ease/repetition state.
* **With history:** an SM-2-style scheme over the four discrete levels. The most
  recent history record supplies the current ``ease_factor``, ``repetitions``
  and last ``interval_days``:

  - **Level 1 (very hard):** lapse — interval resets to the 1-day minimum and the
    ease is decreased (floored at ``EASE_FLOOR`` = 1.3).
  - **Level 2 (hard):** a small, below-ease growth with a slight ease decrease.
  - **Level 3 (good):** standard SM-2 growth ``round(prev_interval * ease)``.
  - **Level 4 (very easy):** SM-2 growth with an easy bonus and an ease increase,
    ``round(prev_interval * ease * EASY_BONUS)``.

* **Clamping (Req 7.1):** every result is an integer clamped into ``[1, 365]``.

* **Monotonicity (Req 7.3) — the central invariant.** For the *same* history,
  ``interval(1) <= interval(2) <= interval(3) <= interval(4)``. This holds by
  construction: the per-level growth multipliers are non-decreasing in the level
  (``0`` for the lapse reset, then ``LEVEL2_FACTOR <= ease <= ease * EASY_BONUS``),
  and the final ``[1, 365]`` clamp uses identical bounds for every level, so it
  preserves ordering.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from app.schemas.difficulty import Difficulty

__all__ = ["compute_next_interval", "ReviewHistoryRecord"]

# --- Tunable constants -------------------------------------------------------

MIN_INTERVAL: int = 1
"""Lower clamp bound, in days (Req 7.1)."""

MAX_INTERVAL: int = 365
"""Upper clamp bound, in days (Req 7.1)."""

EASE_FLOOR: float = 1.3
"""Minimum ease factor; SM-2 never lets ease drop below this."""

EASY_BONUS: float = 1.3
"""Extra multiplier applied on a "very easy" (level 4) review."""

LEVEL2_FACTOR: float = 1.2
"""Growth multiplier for a "hard" (level 2) review. Kept below a typical ease
(default 2.5) so a level-2 interval stays <= the level-3 ``prev * ease``."""

# Initial per-rating interval table used when a word has no review history
# (Req 7.2). Depends only on the rating; non-decreasing in ease.
_INITIAL_INTERVAL: dict[Difficulty, int] = {
    Difficulty.VERY_HARD: 1,
    Difficulty.HARD: 1,
    Difficulty.GOOD: 1,
    Difficulty.VERY_EASY: 4,
}


class ReviewHistoryRecord(Protocol):
    """Structural type for one history entry consumed by the scheduler.

    The scheduler only needs the spaced-repetition state carried forward from a
    prior review: the ease factor, the running count of successful repetitions,
    and the interval that review assigned. Any object exposing these attributes
    (e.g. a persisted ``WordEntry``'s state snapshot or a test double) is a valid
    history record — the scheduler never imports the ORM, keeping it pure and
    trivially testable.
    """

    ease_factor: float
    repetitions: int
    interval_days: int


def _clamp(interval: int) -> int:
    """Clamp an integer interval into ``[MIN_INTERVAL, MAX_INTERVAL]`` (Req 7.1)."""
    return max(MIN_INTERVAL, min(MAX_INTERVAL, interval))


def compute_next_interval(
    rating: Difficulty,
    history: Sequence[ReviewHistoryRecord],
) -> int:
    """Compute the next review interval, in whole days (Req 7.1-7.3).

    Pure: depends only on ``rating`` and ``history`` and has no side effects.

    Args:
        rating: The Learner-selected Difficulty_Rating (``VERY_HARD`` = 1 ..
            ``VERY_EASY`` = 4).
        history: The word's prior review state, oldest-to-newest. The last
            element supplies the current ease, repetitions and last interval.
            An empty sequence means the word has never been reviewed.

    Returns:
        An integer number of days in ``[1, 365]``. For a fixed ``history`` the
        result is non-decreasing in ``rating`` (monotonicity, Req 7.3).
    """
    # No prior history: the interval depends only on the rating (Req 7.2).
    if not history:
        return _clamp(_INITIAL_INTERVAL[rating])

    current = history[-1]
    prev_interval = max(current.interval_days, MIN_INTERVAL)
    ease = max(current.ease_factor, EASE_FLOOR)

    if rating == Difficulty.VERY_HARD:
        # Lapse: reset to the minimum interval regardless of prior growth.
        return _clamp(MIN_INTERVAL)

    if rating == Difficulty.HARD:
        # Small, sub-ease growth.
        return _clamp(round(prev_interval * LEVEL2_FACTOR))

    if rating == Difficulty.GOOD:
        # Standard SM-2 growth.
        return _clamp(round(prev_interval * ease))

    # Difficulty.VERY_EASY: SM-2 growth with the easy bonus.
    return _clamp(round(prev_interval * ease * EASY_BONUS))

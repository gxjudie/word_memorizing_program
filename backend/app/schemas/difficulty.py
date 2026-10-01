"""Shared domain enum for Difficulty_Rating.

``Difficulty`` is the ordered, four-level recall rating used throughout the
system. It lives in its own module so that both the Pydantic schemas
(``app.schemas.*``) and the business-logic services (``app.services.*``) can
import it without creating an import cycle between those layers.
"""

from enum import IntEnum


class Difficulty(IntEnum):
    """Ordered discrete recall rating, hardest=1 .. easiest=4.

    Values are intentionally ``IntEnum`` members so they compare and order like
    integers (``VERY_HARD < HARD < GOOD < VERY_EASY``), which the Scheduler
    relies on for its monotonicity guarantee (Req 7.3), and so request payloads
    carrying the integers 1-4 validate directly into these members (Req 6.2,
    7.1, 8.6).
    """

    VERY_HARD = 1
    HARD = 2
    GOOD = 3
    VERY_EASY = 4

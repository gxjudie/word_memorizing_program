"""Review Service — Review_Session orchestration (Req 6, 7, 8).

This service owns the review loop: selecting the Due_Words for a date, driving
one-at-a-time presentation, and (in task 7.2) recording ratings, scheduling, and
summarizing. Task 7.1 implements due selection and session start plus the
in-session state that later rating logic depends on.

Session state and where it lives
--------------------------------
A Review_Session has transient, per-run state that is *not* part of the durable
domain model: which Due_Word is currently presented, whether its answer has been
revealed (needed for the reveal-before-rate gate in Req 6.5), which words have
already been rated, and the running per-rating tally for the end-of-session
summary (Req 8.4). The durable data — the ``WordEntry`` scheduling state and the
``ReviewRecord`` history — lives in the database and is written when a rating is
recorded (task 7.2).

The design keeps the HTTP API stateless at the persistence layer, so this
transient loop state has no natural ORM home. Two options were considered:

1. A dedicated ``review_sessions`` DB table.
2. A lightweight in-memory registry keyed by ``session_id``.

We choose **(2): an in-memory session registry** keyed by ``session_id``. The
state is short-lived (one review sitting), cheap to reconstruct, and does not
need to survive a process restart; persisting it would add a table and a
migration purely for ephemeral UI-loop bookkeeping. This matches the design's
"session state lives in the DB and client; the API is stateless" split — the
*durable* scheduling writes go to the DB, while the loop cursor is held in
memory. The trade-off is that sessions do not survive a backend restart and are
process-local; for the single-user local/Mac-wrapped build this is acceptable,
and the registry is encapsulated here so it can be swapped for a shared store
(e.g. Redis) without changing callers.

Task 7.2 will extend this same service with ``record_rating``; the
``SessionState`` dataclass below exposes exactly the cursor/reveal/tally fields
that reveal-gating (Req 6.5), advance (Req 8.3), and the summary (Req 8.4) need.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.review_record import ReviewRecord
from app.models.word_entry import WordEntry
from app.schemas.difficulty import Difficulty
from app.schemas.review import (
    NoDueWords,
    RatingResult,
    ReviewSessionOut,
    SessionSummary,
)
from app.schemas.word import WordEntryOut
from app.services.scheduler import (
    EASE_FLOOR,
    ReviewHistoryRecord,
    compute_next_interval,
)

__all__ = [
    "ReviewService",
    "SessionState",
    "SessionNotFoundError",
    "WordNotCurrentError",
    "NotRevealedError",
    "InvalidRatingError",
    "SchedulingNotSavedError",
]


class SessionNotFoundError(Exception):
    """Raised when a ``session_id`` is not present in the registry.

    A session is unknown if it was never started, has already completed, or was
    lost to a backend restart (the registry is in-memory). Rating/advance logic
    in task 7.2 surfaces this to the caller.

    Attributes:
        session_id: The unknown session identifier.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"unknown review session: {session_id!r}")


class WordNotCurrentError(Exception):
    """Raised when a rating/reveal targets a word that is not the current one.

    A Review_Session presents exactly one Target_Word at a time (Req 8.2); an
    operation naming a different (or already-rated) word is rejected and the
    current word is retained unchanged.

    Attributes:
        session_id: The session the operation targeted.
        word_id: The word id the caller supplied.
        current_word_id: The word that is actually current, or ``None`` if the
            session is already complete.
    """

    def __init__(
        self, session_id: str, word_id: str, current_word_id: str | None
    ) -> None:
        self.session_id = session_id
        self.word_id = word_id
        self.current_word_id = current_word_id
        super().__init__(
            f"word {word_id!r} is not the current word "
            f"({current_word_id!r}) in session {session_id!r}"
        )


class NotRevealedError(Exception):
    """Raised when a rating is attempted before the answer is revealed (Req 6.5).

    Rating requires the Target_Word's answer to have been revealed first; until
    then the word stays unrated and the current word is retained.

    Attributes:
        session_id: The session the rating targeted.
        word_id: The current word that was not yet revealed.
    """

    def __init__(self, session_id: str, word_id: str) -> None:
        self.session_id = session_id
        self.word_id = word_id
        super().__init__(
            f"word {word_id!r} must be revealed before rating "
            f"(session {session_id!r})"
        )


class InvalidRatingError(Exception):
    """Raised for a rating value outside the four defined levels (Req 8.6).

    The schema boundary (``RatingInput``/``Difficulty``) rejects out-of-set
    values, but the service guards again so a raw integer never slips through.
    No rating is recorded and the current word is retained.

    Attributes:
        rating: The offending rating value.
    """

    def __init__(self, rating: object) -> None:
        self.rating = rating
        super().__init__(
            f"rating must be one of {{1, 2, 3, 4}}, got {rating!r}"
        )


class SchedulingNotSavedError(Exception):
    """Raised when the transactional scheduling write fails (Req 7.5).

    When the ``ReviewRecord`` + ``WordEntry`` update cannot be committed, the
    transaction is rolled back so the word keeps its previous
    ``next_review_date``; the word stays unrated and the caller surfaces this
    "scheduling not saved" error.

    Attributes:
        word_id: The word whose schedule could not be saved.
    """

    def __init__(self, word_id: str) -> None:
        self.word_id = word_id
        super().__init__(f"scheduling not saved for word {word_id!r}")


# Per-rating ease-factor adjustments applied alongside the Scheduler's interval
# (the Scheduler is pure and returns only the interval). These mirror its
# documented SM-2 behaviour: a lapse (very hard) and a hard review decrease ease,
# good leaves it unchanged, and very easy increases it. The result is floored at
# ``EASE_FLOOR`` so ease never drops below the SM-2 minimum.
_EASE_DELTA: dict[Difficulty, float] = {
    Difficulty.VERY_HARD: -0.20,
    Difficulty.HARD: -0.15,
    Difficulty.GOOD: 0.0,
    Difficulty.VERY_EASY: 0.15,
}


def _next_ease_factor(current_ease: float, rating: Difficulty) -> float:
    """Return the updated ease factor for a rating, floored at ``EASE_FLOOR``.

    Keeps the persisted ease in step with the Scheduler's documented SM-2
    behaviour without duplicating interval math: ease decreases on hard/lapse
    reviews, is unchanged on a good review, and increases on an easy one.
    """
    return max(EASE_FLOOR, current_ease + _EASE_DELTA[rating])


@dataclass
class _HistoryRecord:
    """Carried-forward spaced-repetition state passed to the Scheduler.

    The Scheduler is pure and depends only on the ease/repetitions/interval it
    is handed (see ``ReviewHistoryRecord``); this adapter snapshots the current
    ``WordEntry`` state as the single most-recent history record so the
    Scheduler never imports the ORM.
    """

    ease_factor: float
    repetitions: int
    interval_days: int


@dataclass
class SessionState:
    """Transient state for one in-progress Review_Session.

    Held in the in-memory registry (see module docstring). It tracks the ordered
    Due_Words and a cursor so exactly one Target_Word is "current" at a time
    (Req 8.2), whether that word's answer has been revealed (the reveal gate for
    Req 6.5), which words have been rated (so a word is presented once, Req 8.2),
    and the running per-rating tally for the summary (Req 8.4).

    Attributes:
        session_id: Identifier of this session.
        user_id: Owning Learner; rating writes are authorized against it.
        session_date: The date the session was started; the schedule base date.
        word_ids: Due_Word ids in presentation order (one at a time).
        current_index: Cursor into ``word_ids`` for the current Target_Word.
        revealed: Whether the current word's answer has been revealed (Req 6.5).
        rated_word_ids: Ids of words already rated this session (Req 8.2).
        counts_by_rating: Running tally of recorded ratings (Req 8.4).
    """

    session_id: str
    user_id: str
    session_date: date
    word_ids: list[str]
    current_index: int = 0
    revealed: bool = False
    rated_word_ids: set[str] = field(default_factory=set)
    counts_by_rating: dict[Difficulty, int] = field(default_factory=dict)

    @property
    def current_word_id(self) -> str | None:
        """Id of the Target_Word currently presented, or ``None`` if finished.

        Returns ``None`` once the cursor has advanced past the final Due_Word,
        which signals the session is complete (Req 8.4).
        """
        if 0 <= self.current_index < len(self.word_ids):
            return self.word_ids[self.current_index]
        return None

    @property
    def is_complete(self) -> bool:
        """True when every Due_Word has been rated (no current word remains)."""
        return self.current_word_id is None


class ReviewService:
    """Start Review_Sessions and track their in-progress state (Req 8).

    The service wraps a SQLAlchemy ``Session`` for due selection and the durable
    scheduling writes (task 7.2). In-progress loop state is held in a
    process-wide in-memory registry keyed by ``session_id`` (see module
    docstring); a new ``ReviewService`` instance per request still shares that
    registry, so a session started on one request can be advanced on the next.
    """

    # Process-wide registry of in-progress sessions, keyed by session_id. Shared
    # across ReviewService instances so the stateless-per-request API can resume
    # a session across calls. Encapsulated here so it can be swapped for a
    # shared/external store without changing the public interface.
    _sessions: dict[str, SessionState] = {}

    def __init__(self, db: Session) -> None:
        """Store the SQLAlchemy session used for queries and scheduling writes."""
        self._db = db

    def start_session(
        self,
        user_id: str,
        today: date,
    ) -> ReviewSessionOut | NoDueWords:
        """Start a Review_Session over the Learner's Due_Words (Req 8.1, 8.2, 8.5).

        Due_Words are the Learner's ``WordEntry`` rows whose ``next_review_date``
        is on or before ``today`` (Req 8.1). When none are due, no session is
        created and a ``NoDueWords`` marker is returned (Req 8.5). Otherwise a
        session is registered and the due words are returned in a stable
        presentation order (oldest-created first) so they can be shown one at a
        time (Req 8.2).

        Args:
            user_id: The Learner starting the session.
            today: The current date; the base date for due selection and (in
                task 7.2) scheduling. Injected for testability.

        Returns:
            A ``ReviewSessionOut`` carrying the new ``session_id`` and the due
            words in presentation order, or ``NoDueWords`` when nothing is due.
        """
        due_words = list(
            self._db.execute(
                select(WordEntry)
                .where(
                    WordEntry.user_id == user_id,
                    WordEntry.next_review_date <= today,
                )
                .order_by(WordEntry.created_at, WordEntry.id)
            ).scalars()
        )

        # No Due_Words: do not start a session (Req 8.5).
        if not due_words:
            return NoDueWords()

        session_id = str(uuid.uuid4())
        state = SessionState(
            session_id=session_id,
            user_id=user_id,
            session_date=today,
            word_ids=[word.id for word in due_words],
        )
        self._sessions[session_id] = state

        return ReviewSessionOut(
            session_id=session_id,
            due_words=[WordEntryOut.model_validate(word) for word in due_words],
        )

    def _get_session(self, session_id: str) -> SessionState:
        """Return the registered session state or raise ``SessionNotFoundError``.

        Helper for the rating/advance logic added in task 7.2.
        """
        state = self._sessions.get(session_id)
        if state is None:
            raise SessionNotFoundError(session_id)
        return state

    def reveal(self, session_id: str, word_id: str) -> None:
        """Mark the current Target_Word's answer as revealed (Req 6.5 gate).

        Reveal is the precondition for rating: it flips the session's reveal
        flag so a subsequent ``record_rating`` is allowed. Only the word that is
        currently presented may be revealed — one word is current at a time
        (Req 8.2).

        Args:
            session_id: The session being driven.
            word_id: The word to reveal; must be the current Target_Word.

        Raises:
            SessionNotFoundError: If ``session_id`` is unknown.
            WordNotCurrentError: If ``word_id`` is not the current word.
        """
        state = self._get_session(session_id)
        if word_id != state.current_word_id:
            raise WordNotCurrentError(
                session_id, word_id, state.current_word_id
            )
        state.revealed = True

    def record_rating(
        self,
        user_id: str,
        session_id: str,
        word_id: str,
        rating: Difficulty,
    ) -> RatingResult:
        """Record a Difficulty_Rating, schedule, persist, and advance (Req 7, 8).

        Order of operations (each guard leaves the current word unchanged on
        rejection):

        1. **Resolve the session** and ensure ``word_id`` is the current
           Target_Word (one at a time, Req 8.2).
        2. **Reject out-of-set ratings** (Req 8.6): a value not in {1, 2, 3, 4}
           raises ``InvalidRatingError``; the current word is retained and
           nothing is recorded.
        3. **Require reveal** (Req 6.5): if the current word has not been
           revealed, raise ``NotRevealedError``; the word stays unrated.
        4. **Schedule** via the pure ``compute_next_interval`` using the word's
           carried-forward state as its single history record.
        5. **Persist in one transaction** (Req 7.4): insert a ``ReviewRecord``
           and update the ``WordEntry``'s ``next_review_date`` (= session date +
           interval), ``ease_factor``, ``repetitions`` and ``interval_days``. On
           any failure the transaction is rolled back, the previous
           ``next_review_date`` is retained, and ``SchedulingNotSavedError`` is
           raised with the word left unrated (Req 7.5).
        6. **Advance or summarize** (Req 8.3, 8.4): tally the rating, advance the
           cursor to the next unreviewed Due_Word, and either return the next
           word or, when the final word has been rated, the ``SessionSummary``.

        Args:
            user_id: The Learner recording the rating; must own the word.
            session_id: The session being driven.
            word_id: The word being rated; must be the current Target_Word.
            rating: The selected ``Difficulty`` level (1..4).

        Returns:
            A ``RatingResult`` carrying the stored ``next_review_date`` and
            either the next Due_Word or the end-of-session summary.

        Raises:
            SessionNotFoundError: If ``session_id`` is unknown.
            WordNotCurrentError: If ``word_id`` is not the current word.
            InvalidRatingError: If ``rating`` is outside {1, 2, 3, 4} (Req 8.6).
            NotRevealedError: If the current word was not revealed (Req 6.5).
            SchedulingNotSavedError: If the scheduling write fails (Req 7.5).
        """
        state = self._get_session(session_id)

        # One word is current at a time (Req 8.2): reject a stale/other word so
        # the current word is retained unchanged.
        if word_id != state.current_word_id:
            raise WordNotCurrentError(
                session_id, word_id, state.current_word_id
            )

        # Reject out-of-set ratings; current word retained, nothing recorded
        # (Req 8.6). Guards in-service even though RatingInput validates the
        # schema boundary — a raw int must never slip past.
        try:
            rating = Difficulty(rating)
        except ValueError as exc:
            raise InvalidRatingError(rating) from exc

        # Reveal-before-rate gate (Req 6.5): the word stays unrated until the
        # answer has been revealed.
        if not state.revealed:
            raise NotRevealedError(session_id, word_id)

        # Load the word, scoped to the owning Learner.
        word = self._db.execute(
            select(WordEntry).where(
                WordEntry.id == word_id,
                WordEntry.user_id == user_id,
            )
        ).scalar_one_or_none()
        if word is None:
            # Not the current Learner's word (or vanished): treat as not-current
            # so the session's current word is retained.
            raise WordNotCurrentError(
                session_id, word_id, state.current_word_id
            )

        # Build history from the word's carried-forward state. The latest
        # WordEntry scheduling state *is* the most-recent history record the
        # pure Scheduler consumes; an unreviewed word (repetitions == 0,
        # interval_days == 0) yields the no-history initial table (Req 7.2).
        history: list[ReviewHistoryRecord] = []
        if word.repetitions > 0 or word.interval_days > 0:
            history.append(
                _HistoryRecord(
                    ease_factor=word.ease_factor,
                    repetitions=word.repetitions,
                    interval_days=word.interval_days,
                )
            )

        interval_days = compute_next_interval(rating, history)
        new_next_review_date = state.session_date + timedelta(days=interval_days)

        # Snapshot the previous next_review_date so a failed write can be shown
        # to have retained it (Req 7.5).
        previous_next_review_date = word.next_review_date

        # One transaction: write the ReviewRecord and update the WordEntry's
        # scheduling state together (Req 7.4). Any failure rolls back both so
        # the word keeps its previous schedule and stays unrated (Req 7.5).
        try:
            record = ReviewRecord(
                word_id=word.id,
                rating=int(rating),
                interval_days=interval_days,
            )
            self._db.add(record)
            word.next_review_date = new_next_review_date
            word.interval_days = interval_days
            word.ease_factor = _next_ease_factor(word.ease_factor, rating)
            # A lapse (very hard) resets the running repetition streak; any
            # successful recall increments it.
            word.repetitions = (
                0 if rating == Difficulty.VERY_HARD else word.repetitions + 1
            )
            self._db.commit()
        except Exception as exc:  # pragma: no cover - exercised via injected fault
            self._db.rollback()
            # Retain the previous next_review_date in the live object too, so a
            # caller inspecting it after the failure sees the unchanged value.
            word.next_review_date = previous_next_review_date
            raise SchedulingNotSavedError(word_id) from exc

        # Successful rating: tally and advance (Req 8.3, 8.4).
        state.counts_by_rating[rating] = state.counts_by_rating.get(rating, 0) + 1
        state.rated_word_ids.add(word_id)
        state.revealed = False
        state.current_index += 1

        # Advance past any already-rated words to the next unreviewed Due_Word.
        while (
            state.current_word_id is not None
            and state.current_word_id in state.rated_word_ids
        ):
            state.current_index += 1

        if state.is_complete:
            # Final word rated: produce the end-of-session summary (Req 8.4).
            self._sessions.pop(session_id, None)
            summary = SessionSummary(
                total_reviewed=len(state.rated_word_ids),
                counts_by_rating=dict(state.counts_by_rating),
            )
            return RatingResult(
                word_id=word_id,
                next_review_date=new_next_review_date,
                session_complete=True,
                summary=summary,
            )

        # More Due_Words remain: advance to the next one (Req 8.3).
        next_word = self._db.get(WordEntry, state.current_word_id)
        return RatingResult(
            word_id=word_id,
            next_review_date=new_next_review_date,
            session_complete=False,
            next_word=(
                WordEntryOut.model_validate(next_word)
                if next_word is not None
                else None
            ),
        )

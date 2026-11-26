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

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.review_record import ReviewRecord
from app.models.user import (
    DEFAULT_NEW_WORD_LIMIT,
    DEFAULT_REVIEW_WORD_LIMIT,
    User,
)
from app.models.word_entry import WordEntry
from app.schemas.difficulty import Difficulty
from app.schemas.review import (
    DueWordOut,
    NoDueWords,
    RatingResult,
    RevealedWordOut,
    ReviewSessionOut,
    SessionSummary,
)
from app.services.scheduler import (
    EASE_FLOOR,
    ReviewHistoryRecord,
    compute_next_interval,
)
from app.services.sentence_generation.base import MissingGeminiKeyError

__all__ = [
    "ReviewService",
    "SessionState",
    "SessionNotFoundError",
    "SessionExpiredError",
    "WordNotCurrentError",
    "NotRevealedError",
    "InvalidRatingError",
    "SchedulingNotSavedError",
    "SESSION_TTL",
    "MAX_SESSIONS",
]

# In-progress sessions are transient UI-loop state: they expire after two hours
# so an abandoned tab cannot keep a stale ``session_date`` (and therefore stale
# scheduling) alive indefinitely, and so the registry cannot grow without bound.
SESSION_TTL = timedelta(hours=2)

# Upper bound on retained sessions. When exceeded, the oldest sessions are
# dropped; a dropped session is reported as expired, never as a server error.
MAX_SESSIONS = 1000


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


class SessionExpiredError(Exception):
    """Raised when an in-progress session outlived ``SESSION_TTL``.

    Expired sessions are removed from the registry and are never resumed: the
    stored ``session_date`` would otherwise age, so ratings would schedule from
    a stale base date. The client is told to start a fresh session.

    Attributes:
        session_id: The expired session identifier.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"review session expired: {session_id!r}")


class SessionAccessDeniedError(SessionNotFoundError):
    """Raised when a session belongs to a different Learner.

    Reported exactly like an unknown session so one account cannot probe for
    another account's session identifiers.
    """


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
        expires_at: Instant after which the session is discarded (``SESSION_TTL``).
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
    expires_at: datetime
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
    # Guards the registry: FastAPI runs these sync routes in a thread pool, so
    # concurrent requests can start, advance, and expire sessions at once.
    _lock = threading.RLock()

    def __init__(
        self,
        db: Session,
        *,
        has_gemini_key: Callable[[str], bool] | None = None,
        clock: Callable[[], datetime] | None = None,
        session_ttl: timedelta = SESSION_TTL,
    ) -> None:
        """Store the DB session and the optional Gemini key-presence check.

        Args:
            db: SQLAlchemy session used for queries and scheduling writes.
            has_gemini_key: Returns whether a Learner has a stored
                Gemini_API_Key. The API wiring binds it to the Learner's
                ``gemini_credentials`` row; session start uses it before due
                selection (Req 10.1). ``None`` means no check is made.
            clock: Current instant, injected for tests. Defaults to UTC now.
            session_ttl: How long an in-progress session may live. Defaults to
                the module's ``SESSION_TTL``.
        """
        self._db = db
        self._has_gemini_key = has_gemini_key
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._session_ttl = session_ttl

    def _now(self) -> datetime:
        """Return the current instant as an aware UTC datetime."""
        now = self._clock()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    def _register(self, state: SessionState) -> None:
        """Add ``state`` to the registry, dropping expired and excess sessions."""
        with self._lock:
            now = self._now()
            for session_id, existing in list(self._sessions.items()):
                if existing.expires_at <= now:
                    del self._sessions[session_id]
            # Bound the registry: an abandoned tab must not leak memory forever.
            overflow = len(self._sessions) - MAX_SESSIONS + 1
            if overflow > 0:
                oldest = sorted(
                    self._sessions.items(), key=lambda item: item[1].expires_at
                )[:overflow]
                for session_id, _state in oldest:
                    del self._sessions[session_id]
            self._sessions[state.session_id] = state

    def start_session(
        self,
        user_id: str,
        day: date,
    ) -> ReviewSessionOut | NoDueWords:
        """Start a capped, review-first Review_Session (Req 8.1, 8.2, 8.5, 11).

        Selection uses the Learner's Study_Settings from the ``User`` row
        (``review_word_limit`` and ``new_word_limit``; the defaults when the row
        is missing, which only happens in direct service calls):

        1. Review_Words: words with at least one ``ReviewRecord`` and
           ``next_review_date <= day``, ordered by
           ``(next_review_date, created_at, id)`` and limited to
           ``review_word_limit``. Skipped when that limit is 0 (Req 11.7).
        2. New_Words: words with no ``ReviewRecord``, whatever their
           ``next_review_date``, ordered by ``(created_at, id)`` and limited to
           ``new_word_limit``. Skipped when that limit is 0 (Req 11.6).

        The session presents the Review_Words first, then the New_Words
        (Req 8.1, 8.2). The two groups use ``EXISTS`` and ``NOT EXISTS`` on the
        same correlated subquery, so they are disjoint and no id repeats. An
        empty selection returns ``NoDueWords`` and registers no session
        (Req 8.5).

        Args:
            user_id: The Learner starting the session.
            day: The current Study_Day; the base date for due selection and
                for scheduling (``session_date``). Injected for testability.

        Returns:
            A ``ReviewSessionOut`` carrying the new ``session_id`` and the
            selected words in presentation order, or ``NoDueWords`` when the
            selection is empty.

        Raises:
            MissingGeminiKeyError: If a key-presence check is configured and
                the Learner has no stored Gemini_API_Key. Checked before any
                query, so no session is created (Req 10.1).
        """
        if self._has_gemini_key is not None and not self._has_gemini_key(user_id):
            raise MissingGeminiKeyError()  # Req 10.1, before any query

        user = self._db.get(User, user_id)
        review_limit = (
            user.review_word_limit if user is not None else DEFAULT_REVIEW_WORD_LIMIT
        )
        new_limit = user.new_word_limit if user is not None else DEFAULT_NEW_WORD_LIMIT

        # Correlated EXISTS: does this WordEntry have any ReviewRecord?
        has_review = (
            select(ReviewRecord.id)
            .where(ReviewRecord.word_id == WordEntry.id)
            .exists()
        )

        review_words: list[WordEntry] = []
        if review_limit > 0:  # Req 11.7
            review_words = list(
                self._db.execute(
                    select(WordEntry)
                    .where(
                        WordEntry.user_id == user_id,
                        has_review,
                        WordEntry.next_review_date <= day,  # Due_Word on the Study_Day
                    )
                    .order_by(
                        WordEntry.next_review_date,
                        WordEntry.created_at,
                        WordEntry.id,
                    )
                    .limit(review_limit)
                ).scalars()
            )

        new_words: list[WordEntry] = []
        if new_limit > 0:  # Req 11.6
            new_words = list(
                self._db.execute(
                    select(WordEntry)
                    .where(WordEntry.user_id == user_id, ~has_review)  # any date
                    .order_by(WordEntry.created_at, WordEntry.id)
                    .limit(new_limit)
                ).scalars()
            )

        words = review_words + new_words  # reviews first (Req 8.1, 8.2)

        # Empty selection: do not start a session (Req 8.5).
        if not words:
            return NoDueWords()

        session_id = str(uuid.uuid4())
        state = SessionState(
            session_id=session_id,
            user_id=user_id,
            session_date=day,
            word_ids=[word.id for word in words],
            expires_at=self._now() + self._session_ttl,
        )
        self._register(state)

        return ReviewSessionOut(
            session_id=session_id,
            due_words=[DueWordOut.model_validate(word) for word in words],
        )

    def _get_session(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
    ) -> SessionState:
        """Return the live session state, enforcing expiry and ownership.

        Args:
            session_id: The session being driven.
            user_id: The Learner making the request. When given, a session that
                belongs to another Learner is reported exactly like an unknown
                one (``SessionNotFoundError``), so session ids cannot be probed.

        Raises:
            SessionNotFoundError: ``session_id`` is unknown, already completed,
                or owned by another Learner.
            SessionExpiredError: The session outlived ``SESSION_TTL``; it is
                removed from the registry and must be restarted.
        """
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                raise SessionNotFoundError(session_id)
            if state.expires_at <= self._now():
                del self._sessions[session_id]
                raise SessionExpiredError(session_id)
            if user_id is not None and state.user_id != user_id:
                raise SessionAccessDeniedError(session_id)
            return state

    def current_word_id(self, user_id: str, session_id: str) -> str | None:
        """Return the Target_Word currently presented to ``user_id``.

        Public seam for the sentence route, which needs the session to exist,
        belong to the caller, and still be live before it calls a provider.

        Raises:
            SessionNotFoundError: Unknown session, or one owned by someone else.
            SessionExpiredError: The session outlived ``SESSION_TTL``.
        """
        return self._get_session(session_id, user_id=user_id).current_word_id

    def reveal(self, user_id: str, session_id: str, word_id: str) -> RevealedWordOut:
        """Reveal the current Target_Word and return its answer (Req 6.5 gate).

        Reveal is the precondition for rating and the only place the answer is
        handed to the client: it flips the session's reveal flag so a subsequent
        ``record_rating`` is allowed, and returns the reading and meaning for
        the word that is currently presented (Req 8.2).

        Args:
            user_id: The Learner driving the session; must own it.
            session_id: The session being driven.
            word_id: The word to reveal; must be the current Target_Word.

        Returns:
            The revealed answer as a ``RevealedWordOut``.

        Raises:
            SessionNotFoundError: If ``session_id`` is unknown or not owned by
                ``user_id``.
            SessionExpiredError: If the session outlived ``SESSION_TTL``.
            WordNotCurrentError: If ``word_id`` is not the current word, or the
                word no longer exists in this Learner's collection.
        """
        state = self._get_session(session_id, user_id=user_id)
        if word_id != state.current_word_id:
            raise WordNotCurrentError(
                session_id, word_id, state.current_word_id
            )
        word = self._db.execute(
            select(WordEntry).where(
                WordEntry.id == word_id,
                WordEntry.user_id == user_id,
            )
        ).scalar_one_or_none()
        if word is None:
            # Deleted between selection and reveal: treat as not-current so the
            # session cursor and the reveal gate stay unchanged.
            raise WordNotCurrentError(session_id, word_id, state.current_word_id)
        state.revealed = True
        return RevealedWordOut.model_validate(word)

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
            SessionNotFoundError: If ``session_id`` is unknown or owned by
                another Learner.
            SessionExpiredError: If the session outlived ``SESSION_TTL``.
            WordNotCurrentError: If ``word_id`` is not the current word.
            InvalidRatingError: If ``rating`` is outside {1, 2, 3, 4} (Req 8.6).
            NotRevealedError: If the current word was not revealed (Req 6.5).
            SchedulingNotSavedError: If the scheduling write fails (Req 7.5).
        """
        state = self._get_session(session_id, user_id=user_id)

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
            with self._lock:
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
        next_word = self._db.execute(
            select(WordEntry).where(
                WordEntry.id == state.current_word_id,
                WordEntry.user_id == user_id,
            )
        ).scalar_one_or_none()
        return RatingResult(
            word_id=word_id,
            next_review_date=new_next_review_date,
            session_complete=False,
            next_word=(
                DueWordOut.model_validate(next_word)
                if next_word is not None
                else None
            ),
        )

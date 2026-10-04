# Implementation Plan: Japanese Vocabulary Trainer

## Overview

This plan builds the Japanese Vocabulary Trainer as a monorepo (`backend/` FastAPI + Python, `frontend/` React + TypeScript + Vite), following the layer separation from the design (`api/`, `services/`, `models/`, `schemas/`, `auth/`, `db/`).

The backend is built first, bottom-up, so each layer is independently testable without HTTP or a running provider: scaffolding → ORM models + migrations → Pydantic schemas → the **pure** Scheduler → Word Service → Sentence Generation Service + OpenAI adapter → Review Service / session orchestration → Auth + LLM proxy → API routes that wire everything together. The frontend then consumes the typed API: client → Add Word view → Review Session view → Sentence Renderer → Lookup Panel → Reveal + Rating controls.

Property-based tests use **Hypothesis** via **pytest**, configured for a minimum of 100 iterations (`@settings(max_examples=100)`). Each property test implements exactly one property and carries the tag comment:
`# Feature: japanese-vocab-trainer, Property {number}: {property_text}`
Example and integration tests cover the failure branches (timeout/retry, scheduling-write failure, no-due-words, malformed import).

## Tasks

- [x] 1. Backend scaffolding and configuration
  - Create the `backend/` package structure (`app/`, `app/api/routes/`, `app/schemas/`, `app/models/`, `app/services/sentence_generation/`, `app/auth/`, `app/db/`, `tests/properties/`, `tests/unit/`, `tests/integration/`) with `__init__.py` files
  - Write `pyproject.toml` with dependencies (fastapi, uvicorn, pydantic, pydantic-settings, sqlalchemy, alembic, passlib[argon2], python-jose, openai, pytest, hypothesis, httpx) and tool config (pytest, Hypothesis min 100 examples)
  - Create `app/config.py` with a Pydantic `BaseSettings` reading `DB_URL`, `OPENAI_API_KEY`, `JWT_SECRET`; add `.env.example`
  - Create `app/db/session.py` with the SQLAlchemy engine and session factory (PostgreSQL via `DB_URL`, SQLite for local/tests)
  - Create `app/main.py` with the FastAPI app factory and empty router registration placeholder
  - _Requirements: non-functional foundation for all requirements_

- [x] 2. Data models and migrations
  - [x] 2.1 Implement SQLAlchemy ORM models
    - Create `app/models/user.py` (`User`: id, email unique, password_hash nullable, created_at)
    - Create `app/models/word_entry.py` (`WordEntry`: id, user_id FK, word_form, reading, meaning, next_review_date, ease_factor default 2.5, interval_days default 0, repetitions default 0, created_at; unique constraint on `(user_id, word_form)`)
    - Create `app/models/review_record.py` (`ReviewRecord`: id, word_id FK, rating, interval_days, reviewed_at)
    - Create `app/models/generated_sentence.py` (`GeneratedSentence`: id, word_id FK, text, tokens JSON, created_at)
    - Define relationships: User 1—* WordEntry, WordEntry 1—* ReviewRecord, WordEntry 1—* GeneratedSentence
    - _Requirements: 1.1, 1.4, 1.5, 7.4, 4.3_

  - [x] 2.2 Configure Alembic and create the initial migration
    - Initialize `alembic/` wired to `app/db/session.py` metadata
    - Generate the initial migration creating all four tables and the `(user_id, word_form)` unique constraint
    - _Requirements: 1.4, 7.4_

- [x] 3. Pydantic schemas and the Difficulty enum
  - [x] 3.1 Define domain and request/response schemas
    - Create `app/schemas/word.py` (`WordInput` with field bounds: word_form 1–50, reading 1–100, meaning 1–500, non-whitespace; `WordEntryOut`)
    - Create `app/schemas/review.py` (`ReviewSessionOut`, `RatingInput`, `RatingResult`, `SessionSummary`, `NoDueWords`)
    - Define the `Difficulty` `IntEnum` (VERY_HARD=1 .. VERY_EASY=4) in a shared module reused by schemas and services
    - _Requirements: 1.1, 1.2, 1.3, 6.2, 7.1, 8.4_

  - [x] 3.2 Implement the LLM structured-output schema with validators
    - Create `app/schemas/sentence.py` with `SentenceTokenSchema` (surface/reading min_length 1, meaning optional, is_target, contains_kanji) and `GeneratedSentenceSchema` (text min_length 1, tokens 1–50) plus the `_must_contain_target` model validator and `_reconstruction_sanity` field validator
    - Expose `SentenceToken` / `GeneratedSentence` domain models used by services
    - _Requirements: 4.1, 4.4, 4.6, 3.2, 5.1, 5.2, 5.4_

  - [ ]* 3.3 Write property test for word input validation
    - **Property 2: Invalid word is rejected naming exactly the offending fields**
    - **Validates: Requirements 1.2, 1.3**
    - Hypothesis strategy generates inputs with arbitrary subsets of fields missing/whitespace/over-length; assert the error names exactly that subset

  - [ ]* 3.4 Write property test for accepted sentence length
    - **Property 12: Accepted sentence length is between 1 and 50 words**
    - **Validates: Requirements 4.4**
    - Generate token lists of varied length; assert `GeneratedSentenceSchema` accepts iff 1–50 tokens

- [x] 4. Implement the pure Scheduler
  - [x] 4.1 Implement `compute_next_interval`
    - Create `app/services/scheduler.py` with the pure `compute_next_interval(rating, history) -> int`: SM-2-style growth per level, no-history initial table (L1→1, L2→1, L3→1, L4→4), ease floor 1.3, easy bonus, clamped to `[1, 365]`, integer days; no side effects
    - _Requirements: 7.1, 7.2, 7.3_

  - [ ]* 4.2 Write property test for interval clamping
    - **Property 13: Scheduling interval is clamped to [1, 365] days**
    - **Validates: Requirements 7.1**
    - Generate ratings 1–4 and arbitrary histories; assert `1 <= interval <= 365` and integer

  - [ ]* 4.3 Write property test for initial interval
    - **Property 14: Initial interval depends only on the rating**
    - **Validates: Requirements 7.2**
    - Generate entries with no history across all ratings; assert interval equals the fixed per-rating value regardless of other state

  - [ ]* 4.4 Write property test for monotonicity
    - **Property 15: Interval is monotonic non-decreasing in ease**
    - **Validates: Requirements 7.3**
    - For a fixed arbitrary history, assert `interval(1) <= interval(2) <= interval(3) <= interval(4)`

- [x] 5. Implement the Word Service
  - [x] 5.1 Implement add/list with validation, dedupe, and due-today
    - Create `app/services/word_service.py` implementing `WordService.add_word` (validate via `WordInput`, exact-match dedupe per user raising `DuplicateError`, set `next_review_date = today`) and `list_collection`
    - Raise `ValidationError` naming exactly the offending fields; `DuplicateError` leaving the existing entry unmodified
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 5.2 Write property test for faithful word creation
    - **Property 1: Valid word is created and stored faithfully**
    - **Validates: Requirements 1.1**
    - Generate in-bounds non-whitespace inputs; assert stored word_form/reading/meaning equal the input

  - [ ]* 5.3 Write property test for duplicate rejection
    - **Property 3: Duplicate word form is rejected without modifying the existing entry**
    - **Validates: Requirements 1.4**
    - Generate a collection + a new input matching an existing word_form; assert rejection, unchanged size, unmodified existing entry

  - [ ]* 5.4 Write property test for immediate due date
    - **Property 4: Newly created words are due immediately**
    - **Validates: Requirements 1.5**
    - Generate valid words added on an arbitrary date; assert `next_review_date <= that date`

- [x] 6. Implement the Sentence Generation Service and OpenAI adapter
  - [x] 6.1 Define the SentenceGenerationService interface and errors
    - Create `app/services/sentence_generation/base.py` with the async `SentenceGenerationService` ABC (`generate(target, previous)`), plus `GenerationTimeout` and `GenerationError` exception types
    - _Requirements: 4.1, 4.2, 4.5, 4.6_

  - [x] 6.2 Implement the validate/retry pipeline in the OpenAI adapter
    - Create `app/services/sentence_generation/openai_adapter.py`: inject server-held key, request structured JSON, re-validate with `GeneratedSentenceSchema`, enforce target presence, enforce difference-from-previous (at least one non-target word), enforce 1–50 tokens, retry up to 3 times, raise `GenerationError` on exhaustion and `GenerationTimeout` past the 5s budget
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 6.3 Write property test for target-presence retry budget
    - **Property 10: Accepted sentence contains the target within the retry budget**
    - **Validates: Requirements 4.1, 4.6**
    - With a mocked provider yielding sequences of outputs, assert the first schema-valid target-containing output is accepted within ≤3 retries, else `GenerationError`

  - [ ]* 6.4 Write property test for difference-from-previous
    - **Property 11: New sentence differs from the previous by a non-target word**
    - **Validates: Requirements 4.3**
    - Generate previous/candidate pairs; assert acceptance iff they differ in at least one non-target word

  - [ ]* 6.5 Write integration test for the timeout + retry branch
    - Use a simulated slow/failing provider; assert `GenerationTimeout` past 5s and that exhausted retries raise `GenerationError` (never calling the real API)
    - _Requirements: 4.2, 4.5, 4.6_

- [x] 7. Implement the Review Service and session orchestration
  - [x] 7.1 Implement due selection and session start
    - Create `app/services/review_service.py` implementing `start_session` (due = `next_review_date <= today`; empty → `NoDueWords`) and session state tracking which word is current/revealed
    - _Requirements: 8.1, 8.2, 8.5_

  - [x] 7.2 Implement rating recording, scheduling, and transactional persistence
    - Implement `record_rating`: reject out-of-set ratings (retain current word), require revealed (reject otherwise, word stays unrated), call the Scheduler, write the `ReviewRecord` + updated `next_review_date`/`ease_factor`/`repetitions` in one transaction, advance to the next unreviewed due word or produce the session summary; on write failure retain the previous `next_review_date` and surface a "scheduling not saved" error
    - _Requirements: 6.5, 7.4, 7.5, 8.2, 8.3, 8.4, 8.6_

  - [ ]* 7.3 Write property test for due selection
    - **Property 17: Due selection is exactly the non-future words**
    - **Validates: Requirements 8.1**
    - Generate collections with varied next-review dates and a current date; assert the selected set is exactly those `<= today`

  - [ ]* 7.4 Write property test for one-at-a-time presentation
    - **Property 18: Session presents each due word once, one at a time**
    - **Validates: Requirements 8.2, 8.3**
    - Drive a session to completion; assert exactly one current word at a time, each due word presented once, non-final rating advances to an unreviewed word

  - [ ]* 7.5 Write property test for session summary counts
    - **Property 19: Session summary counts are exact**
    - **Validates: Requirements 8.4**
    - Complete sessions with varied rating sequences; assert total reviewed = due count and per-rating counts match submissions

  - [ ]* 7.6 Write property test for stored schedule date
    - **Property 16: Stored next review date matches the computed schedule**
    - **Validates: Requirements 7.4**
    - For successful ratings on an arbitrary date, assert stored `next_review_date == date + computed interval`

  - [ ]* 7.7 Write property test for invalid rating rejection
    - **Property 20: Invalid rating values are rejected and the word is retained**
    - **Validates: Requirements 8.6**
    - Generate values outside {1,2,3,4}; assert rejection, no rating recorded, current word retained

  - [ ]* 7.8 Write example tests for failure branches
    - Scheduling-write failure (7.5): injected transaction failure retains previous date + "scheduling not saved" error, word unrated
    - No-due-words (8.5): empty due set returns the no-session result
    - _Requirements: 7.5, 8.5_

- [x] 8. Implement authentication and the LLM proxy wiring
  - [x] 8.1 Implement password hashing and JWT
    - Create `app/auth/security.py` with passlib[argon2] hashing/verification and python-jose JWT issue/verify helpers
    - Implement the Auth Service login/logout issuing a session/JWT
    - _Requirements: auth model (backend-owned identity)_

  - [x] 8.2 Implement API dependencies for current user, DB session, and service wiring
    - Create `app/api/deps.py` providing the current authenticated Learner (from Bearer JWT), a DB session, and factory wiring for WordService / ReviewService / SentenceGenerationService (OpenAI adapter with server-held key)
    - _Requirements: auth model; backend-mediated LLM access_

  - [ ]* 8.3 Write integration test for the authenticated LLM proxy boundary
    - Assert sentence routes require auth, the server-held key is injected server-side and never returned to the client
    - _Requirements: auth model, 4.1_

- [x] 9. Implement API routes (wire backend layers to HTTP)
  - [x] 9.1 Implement auth routes
    - Create `app/api/routes/auth.py` with `POST /api/auth/login` and `POST /api/auth/logout`; register the router in `app/main.py`
    - _Requirements: auth model_

  - [x] 9.2 Implement word routes
    - Create `app/api/routes/words.py` with `POST /api/words` (400 per-field errors, 409 duplicate) and `GET /api/words`; register the router
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 9.3 Implement review routes
    - Create `app/api/routes/review.py` with `POST /api/review/sessions` (due or no-due), `POST /api/review/sessions/:id/words/:wordId/sentence` (proxy generation, 3/4 handling, timeout/error → generation-error), `POST /api/review/sessions/:id/words/:wordId/rating` (reveal-gating 6.5, invalid-rating rejection 8.6, schedule + advance/summarize); register the router
    - _Requirements: 3.1, 3.5, 4.1, 4.2, 4.5, 4.6, 6.5, 7.4, 7.5, 8.1, 8.3, 8.4, 8.5, 8.6_

  - [ ]* 9.4 Write integration test for the end-to-end session flow
    - Against a test database with a mocked provider: start session → generate sentence → reveal → rate → advance → summary; assert auth-protected access
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [x] 10. Checkpoint - backend complete
  - Ensure all backend tests pass, ask the user if questions arise.

- [x] 11. Frontend scaffolding and typed API client
  - [x] 11.1 Scaffold the Vite React + TypeScript app
    - Create `frontend/` with Vite + React + TS, Tailwind (or CSS modules), `package.json`, `index.html`, and `src/{components,views,api}/`
    - _Requirements: frontend foundation_

  - [x] 11.2 Implement the typed API client
    - Create `src/api/` with typed client functions for auth, words, import, session start, sentence generation, and rating, including shared TypeScript types mirroring the backend schemas (tokens, GeneratedSentence, Difficulty)
    - Map `GenerationTimeout`/`GenerationError` responses to a generation-error state
    - _Requirements: 4.5, 4.6, backend API contract_

- [x] 12. Implement the Add Word view
  - [x] 12.1 Build the Add Word form
    - Create `src/views/AddWord` posting to `POST /api/words`, surfacing per-field validation errors (1.2, 1.3) and the duplicate error (1.4)
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [ ]* 12.2 Write UI test for per-field error display
    - Assert each named invalid field is highlighted and duplicate errors are shown
    - _Requirements: 1.2, 1.3, 1.4_

- [x] 13. Implement the Sentence Renderer
  - [x] 13.1 Render tokens with target emphasis and furigana toggle
    - Create `src/components/SentenceRenderer`: each token is an interactive span; `isTarget` tokens get an emphasis style differing in at least one observable attribute (color/weight/background); a per-sentence "show furigana" toggle renders `reading` in parentheses
    - Render the "no example available" state when no sentence is present (3.5)
    - _Requirements: 3.1, 3.2, 3.5_

  - [ ]* 13.2 Write UI test for target emphasis
    - **Property 6: Target word is always emphasized distinctly**
    - **Validates: Requirements 3.2**
    - For generated token arrays, assert every `isTarget` token differs from non-target tokens in at least one observable attribute

- [x] 14. Implement the Lookup Panel
  - [x] 14.1 Build the Lookup Panel
    - Create `src/components/LookupPanel`: on selecting a non-target token show its meaning and reading (and furigana when `containsKanji`); replace content on new selection; show the unavailable message and clear prior content when meaning is None; persist until another selection or dismissal; wire selection from the Sentence Renderer
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 14.2 Write UI test for lookup panel state transitions
    - **Property 8: Lookup panel reflects only the most recent selection**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5**
    - For sequences of selections, assert the panel shows exactly the most recent token's content, clears on unavailable, and clears on dismissal

- [x] 15. Implement the Reveal + Rating controls and Review Session view
  - [x] 15.1 Build the Reveal + Rating controls
    - Create `src/components/RatingControls`: reveal shows the Target_Word reading + meaning; rating options appear only after reveal; offer exactly four ordered levels (very hard=1 .. very easy=4); selecting a level sends the rating and visually marks the selection; surface the "rating not saved" and "invalid rating" errors
    - _Requirements: 3.3, 3.4, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 8.6_

  - [x] 15.2 Build the Review Session view (orchestration)
    - Create `src/views/ReviewSession`: start the session (show "no words due" when empty), present one Target_Word at a time, request a sentence (with retry control on generation error), compose Sentence Renderer + Lookup Panel + Rating controls, advance on rating, and render the end-of-session summary (total reviewed + per-rating counts)
    - _Requirements: 4.5, 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ]* 15.3 Write UI tests for reveal-then-rate flow
    - **Property 7: Reveal gating controls meaning and rating visibility**
    - **Validates: Requirements 3.1, 3.3, 3.4, 6.1, 6.3, 6.5**
    - **Property 9: Exactly four ordered rating options after reveal**
    - **Validates: Requirements 6.2, 6.4**
    - Assert meaning/rating are withheld pre-reveal and shown post-reveal, and exactly four ordered options are offered with selection marked

- [x] 16. Checkpoint - full stack wired
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 17. (OPTIONAL) Implement Anki deck import
  - [ ]* 17.1 Implement the import service with exact accounting
    - Add import logic to `app/services/word_service.py`: for a well-formed deck create one `WordEntry` per record with a non-empty word_form, skip missing-field records (missing-field skip), skip duplicates against existing entries and earlier records in the same import (duplicate skip), and return a summary with created / skippedMissingField / skippedDuplicate counts; abort the whole import with an invalid-file error on an unreadable/unrecognized file
    - Add `POST /api/words/import` (feature-flagged) and the frontend import entry point in the Add Word view behind a flag
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ]* 17.2 Write property test for import accounting
    - **Property 5: Import accounting is exact (optional feature)**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.5**
    - For generated well-formed decks, assert `created + skippedMissingField + skippedDuplicate == totalRecordsProcessed` and created equals the non-empty, non-duplicate count

  - [ ]* 17.3 Write example test for malformed import abort
    - Assert an unreadable/unrecognized file aborts the import with zero writes and an invalid-file error
    - _Requirements: 2.4_

## Notes

- Tasks marked with `*` are optional (tests and the optional Anki import feature) and can be skipped for a faster MVP; core implementation tasks are never optional.
- The model MUST NOT implement sub-tasks postfixed with `*`.
- Each task references the specific requirements and/or correctness properties it implements for traceability.
- Property tests use Hypothesis with `@settings(max_examples=100)` and the per-property tag comment: `# Feature: japanese-vocab-trainer, Property {number}: {property_text}`.
- The pure `scheduler.py` and the Pydantic LLM-output validator are isolated units, making Properties 10–15 fast and deterministic; the provider is always mocked in tests and never called for real.
- Checkpoints ensure incremental validation at the backend-complete and full-stack-wired boundaries.
- All 8 requirements and all 20 correctness properties are covered: Properties 1–5 (word service + import), 6–9 (frontend render/lookup/reveal/rating), 10–12 (sentence generation), 13–16 (scheduler + persistence), 17–20 (review session).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1", "3.1", "3.2", "4.1", "6.1"] },
    { "id": 1, "tasks": ["2.2", "3.3", "3.4", "4.2", "4.3", "4.4", "5.1", "6.2", "8.1"] },
    { "id": 2, "tasks": ["5.2", "5.3", "5.4", "6.3", "6.4", "6.5", "7.1", "8.2"] },
    { "id": 3, "tasks": ["7.2", "8.3", "17.1"] },
    { "id": 4, "tasks": ["7.3", "7.4", "7.5", "7.6", "7.7", "7.8", "17.2", "17.3"] },
    { "id": 5, "tasks": ["9.1", "9.2", "9.3"] },
    { "id": 6, "tasks": ["9.4", "11.1"] },
    { "id": 7, "tasks": ["11.2"] },
    { "id": 8, "tasks": ["12.1", "13.1", "14.1", "15.1"] },
    { "id": 9, "tasks": ["12.2", "13.2", "14.2", "15.2"] },
    { "id": 10, "tasks": ["15.3"] }
  ]
}
```

# Implementation Plan: Japanese Vocabulary Trainer

## Overview

This plan builds the Japanese Vocabulary Trainer as a monorepo (`backend/` FastAPI + Python, `frontend/` React + TypeScript + Vite), following the layer separation from the design (`api/`, `services/`, `models/`, `schemas/`, `auth/`, `db/`).

The backend is built first, bottom-up, so each layer is independently testable without HTTP or a running provider: scaffolding → ORM models + migrations → Pydantic schemas → the **pure** Scheduler → Word Service → Sentence Generation Service + Gemini adapter → Review Service / session orchestration → Google sign-in + Gemini key service + BYOK LLM proxy → API routes that wire everything together. The frontend then consumes the typed API: client → Login view → Gemini key setup (Key_Setup_Guide) → Add Word view → Review Session view → Sentence Renderer → Lookup Panel → Reveal + Rating controls.

Property-based tests use **Hypothesis** via **pytest**, configured for a minimum of 100 iterations (`@settings(max_examples=100)`). Each property test implements exactly one property and carries the tag comment:
`# Feature: japanese-vocab-trainer, Property {number}: {property_text}`
Example and integration tests cover the failure branches (timeout/retry, scheduling-write failure, no-due-words, malformed import).

## Tasks

- [x] 1. Backend scaffolding and configuration
  - **Auth-model update (Google + Gemini BYOK):** reopened. The existing OpenAI "Sign in with ChatGPT" settings and dependencies are replaced. Only `pyproject.toml`, `app/config.py`, and `.env.example` change; the package layout, `app/db/session.py`, and `app/main.py` stay as implemented.
  - Create the `backend/` package structure (`app/`, `app/api/routes/`, `app/schemas/`, `app/models/`, `app/services/sentence_generation/`, `app/auth/`, `app/db/`, `tests/properties/`, `tests/unit/`, `tests/integration/`) with `__init__.py` files
  - Update `pyproject.toml`: **remove `openai` and `authlib`**; keep fastapi, uvicorn, pydantic, pydantic-settings, sqlalchemy, alembic, httpx, python-jose[cryptography], cryptography, pytest, hypothesis and the tool config (pytest, Hypothesis min 100 examples). `httpx` drives the Google PKCE/code-exchange client, the JWKS fetch, and every Gemini call; `python-jose` validates the Google ID token and signs the app session; `cryptography` (Fernet) encrypts the auth-start cookie and the stored Gemini keys; **no `passlib[argon2]`** (no local passwords)
  - Update `app/config.py` `Settings` to read `DB_URL`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` (`SecretStr`), `OAUTH_REDIRECT_URI` (default `http://localhost:5173/api/auth/callback`), `SESSION_SECRET`, `TOKEN_ENCRYPTION_KEY`, and `GEMINI_MODEL` (default `gemini-flash-lite-latest`); **remove `OPENAI_OAUTH_CLIENT_ID` and `EXT_AGENT_HOST_ID`** (including the generate-and-persist-at-first-run logic); expose **no Gemini key field** (no developer-held key); Google and Gemini endpoint URLs are code constants, not settings. Update `.env.example` to the same variable list
  - Create `app/db/session.py` with the SQLAlchemy engine and session factory (PostgreSQL via `DB_URL`, SQLite for local/tests)
  - Create `app/main.py` with the FastAPI app factory and empty router registration placeholder
  - _Requirements: non-functional foundation for all requirements; 9.1, 10.16, 10.17_

- [x] 2. Data models and migrations
  - [x] 2.1 Implement SQLAlchemy ORM models
    - **Auth-model update (Google + Gemini BYOK):** reopened. Delta from the existing OpenAI models: `User` drops `oidc_client_id`, `OAuthCredential` is removed, and `GeminiCredential` and `RevokedAppSession` are added.
    - Update `app/models/user.py` (`User`: id, email, name nullable, oidc_issuer (stored normalized as `https://accounts.google.com`), oidc_subject, created_at; **remove `oidc_client_id`**; **no `password_hash`**; unique constraint `uq_user_oidc_subject` on `(oidc_issuer, oidc_subject)` — one app account per verified Google identity)
    - Delete `app/models/oauth_credential.py` and remove `OAuthCredential` from `app/models/__init__.py` and from the `User` relationships
    - Create `app/models/gemini_credential.py` (`GeminiCredential`: id, user_id unique FK → `users.id` ON DELETE CASCADE, api_key_encrypted TEXT NOT NULL (Fernet ciphertext of the trimmed key), key_hint VARCHAR(4) NOT NULL (last 4 chars), created_at, updated_at bumped on replace)
    - Create `app/models/revoked_session.py` (`RevokedAppSession`: sid VARCHAR(64) primary key, expires_at DATETIME NOT NULL, indexed)
    - Create `app/models/word_entry.py` (`WordEntry`: id, user_id FK, word_form, reading, meaning, next_review_date, ease_factor default 2.5, interval_days default 0, repetitions default 0, created_at; unique constraint on `(user_id, word_form)`)
    - Create `app/models/review_record.py` (`ReviewRecord`: id, word_id FK, rating, interval_days, reviewed_at)
    - Create `app/models/generated_sentence.py` (`GeneratedSentence`: id, word_id FK, text, tokens JSON, created_at)
    - Define relationships: User 1—0..1 GeminiCredential, User 1—* WordEntry, WordEntry 1—* ReviewRecord, WordEntry 1—* GeneratedSentence
    - _Requirements: 1.1, 1.4, 1.5, 7.4, 4.3, 9.9, 9.12, 9.13, 10.11, 10.12_

  - [x] 2.2 Add the Google sign-in + Gemini BYOK Alembic revision
    - **Auth-model update (Google + Gemini BYOK):** reopened. The initial migration `fc993ac91694_initial_schema.py` is already applied to developer databases (`jvt.db`) and is **not rewritten**; a new revision carries the delta.
    - Create `alembic/versions/<rev>_google_signin_gemini_byok.py` with `down_revision = "fc993ac91694"`, using `op.batch_alter_table` so it runs on SQLite. Upgrade: drop `oauth_credentials`; create `gemini_credentials` (id PK, user_id unique FK ON DELETE CASCADE, api_key_encrypted TEXT NOT NULL, key_hint VARCHAR(4) NOT NULL, created_at, updated_at); create `revoked_app_sessions` (sid VARCHAR(64) PK, expires_at DATETIME NOT NULL, indexed); on `users` drop `uq_user_oidc_subject`, drop the `oidc_client_id` column, and recreate `uq_user_oidc_subject` on `(oidc_issuer, oidc_subject)`
    - Downgrade: recreate an empty `oauth_credentials`, re-add `oidc_client_id` with `server_default=''`, restore the three-column unique constraint, and drop the two new tables
    - _Requirements: 9.9, 9.12, 9.13, 10.11_

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

- [x] 6. Implement the Sentence Generation Service and Gemini adapter
  - [x] 6.1 Define the SentenceGenerationService interface and errors
    - **Auth-model update (Google + Gemini BYOK):** reopened. The OpenAI plan-usage errors and the access-token source are replaced by Gemini BYOK errors and an injected key provider.
    - In `app/services/sentence_generation/base.py`: keep the async `SentenceGenerationService` ABC (`generate(user_id, target, previous)`); add the `SentenceGenerationError` base class; keep `GenerationTimeout` and `GenerationError` as its subclasses; add `MissingGeminiKeyError` (Req 4.7, 10.15), `InvalidGeminiKeyError` (Req 4.9), and `QuotaExceededError` (Req 4.8); **remove `PlanIneligibleError`, `UsageCapExceededError`, and `TokenRefreshError`** and update every import of them (services, routes, auth)
    - Define the `ApiKeyProvider` Protocol (`__call__(user_id) -> str`, raising `MissingGeminiKeyError` when no key is stored) to replace the access-token provider; every exception message is a fixed string that never contains a key
    - _Requirements: 4.1, 4.2, 4.5, 4.7, 4.8, 4.9, 4.10, 4.11, 10.13, 10.16_

  - [x] 6.2 Implement the validate/retry pipeline in the Gemini adapter
    - **Auth-model update (Google + Gemini BYOK):** reopened. `OpenAIAdapter` (`openai_adapter.py`, Responses API on the plan-usage token) is replaced by `GeminiAdapter`; the existing validate/retry checks carry over.
    - Create `app/services/sentence_generation/gemini_adapter.py` and delete `openai_adapter.py`: inject an `ApiKeyProvider`, an `httpx.AsyncClient`, and the model ID (`GEMINI_MODEL`); call the provider **once per generation request** so `MissingGeminiKeyError` propagates before any HTTP request; each attempt is one `POST https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent` with the `x-goog-api-key` header and a body carrying `systemInstruction`, the user prompt (target + previous sentence), and `generationConfig = {responseMimeType: "application/json", responseSchema: GEMINI_RESPONSE_SCHEMA}`
    - Add `GEMINI_RESPONSE_SCHEMA` (OpenAPI subset mirroring `GeneratedSentenceSchema`) to `app/schemas/sentence.py`
    - The `systemInstruction` asks for each token's `meaning` in Korean (한국어), while `text`, `surface`, and `reading` stay Japanese; when a previous sentence exists for the word, the user prompt includes it and asks for a different sentence
    - Parse `candidates[0].content.parts[0].text` as JSON and re-validate with `GeneratedSentenceSchema`; treat no candidates, a `promptFeedback.blockReason`, a `finishReason` other than `STOP`, invalid JSON, or a schema failure as a discarded output; keep target presence and 1–50 tokens; discard a candidate as identical to the previous sentence only when its `text`, after trimming leading and trailing whitespace, equals the trimmed `text` of the immediately preceding `GeneratedSentence` for that word (any other difference is accepted); allow 1 additional attempt per generation request (at most 2 attempts), then raise `GenerationError`
    - Bound each attempt at 15 s with `asyncio.wait_for` around the httpx call; a timeout raises `GenerationTimeout` and ends the request, and a late response is dropped
    - Create `app/services/gemini_errors.py` with the pure `classify_generation_failure(status, body)`: 429 / `RESOURCE_EXHAUSTED` → `QuotaExceededError` (stop, no further attempts); 400 `API_KEY_INVALID`, 401, 403 `PERMISSION_DENIED` → `InvalidGeminiKeyError` (stop, no further attempts); other 4xx/5xx and transport errors → retry within the same allowance, ending in `GenerationError`
    - Never log the key or put it in an exception message: re-raise httpx errors as the app's own types with `from None`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.8, 4.9, 4.10, 4.11, 5.6, 10.13, 10.16, 10.17_

  - [ ]* 6.3 Write property test for target-presence retry budget
    - **Property 10: Accepted sentence contains the target within the retry budget**
    - **Validates: Requirements 4.1, 4.6, 4.10**
    - With a mocked provider yielding sequences of outputs, assert the first schema-valid target-containing output is accepted within at most 1 additional attempt, else `GenerationError`

  - [ ]* 6.4 Write property test for difference-from-previous
    - **Property 11: New sentence is not identical to the previous one**
    - **Validates: Requirements 4.3, 4.6**
    - Generate previous/candidate text pairs (identical, identical except for leading or trailing whitespace including U+3000, differing by one character, by internal whitespace, or by one token); assert a candidate is discarded exactly when its trimmed text equals the previous trimmed text

  - [ ]* 6.5 Write integration test for the timeout + retry branch
    - Use a simulated slow/failing provider; assert `GenerationTimeout` past 15 s and that exhausted retries raise `GenerationError` (never calling the real API)
    - Assert `GEMINI_RESPONSE_SCHEMA` and `GeneratedSentenceSchema` declare the same field names and required sets
    - _Requirements: 4.2, 4.5, 4.6_

  - [ ]* 6.6 Write property test for Gemini generation error mapping
    - **Property 24: Gemini generation errors map correctly and stop or retry as required**
    - **Validates: Requirements 4.8, 4.9, 4.11**
    - Generate sequences of mocked `generateContent` responses (via `httpx.MockTransport`) drawn from {valid sentence, discarded output, quota rejection (429 or `RESOURCE_EXHAUSTED`), invalid-key rejection (400 `API_KEY_INVALID`, 401, 403 `PERMISSION_DENIED`), transient error (other 4xx, 5xx, transport error)}; assert requests stop at the first terminal outcome, quota and invalid-key rejections raise `QuotaExceededError` / `InvalidGeminiKeyError` with no later request, budget exhaustion raises `GenerationError` after exactly 2 requests, and a valid sentence within the budget is returned

- [x] 7. Implement the Review Service and session orchestration
  - [x] 7.1 Implement due selection and session start
    - Create `app/services/review_service.py` implementing `start_session` (due = `next_review_date <= today`; empty → `NoDueWords`) and session state tracking which word is current/revealed
    - _Requirements: 8.1, 8.2, 8.5_

  - [x] 7.2 Implement rating recording, scheduling, and transactional persistence
    - Implement `record_rating`: reject out-of-set ratings (retain current word), require revealed (reject otherwise, word stays unrated), call the Scheduler, write the `ReviewRecord` + updated `next_review_date`/`ease_factor`/`repetitions` in one transaction, advance to the next unreviewed due word or produce the session summary; on write failure retain the previous `next_review_date` and surface a "scheduling not saved" error
    - _Requirements: 6.5, 7.4, 7.5, 8.2, 8.3, 8.4, 8.6_

  - [ ]* 7.3 Write property test for due selection
    - **Property 17: Review/New classification follows review history**
    - **Validates: Requirements 8.1**
    - Generate collections with varied next-review dates, zero or more ReviewRecords per word, and independent `repetitions`/`interval_days`; with limits at least the collection size, assert the Review_Words are exactly the reviewed words with `next_review_date <= d` and the New_Words are exactly the words with no ReviewRecord, whatever their date

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

- [x] 8. Implement Google sign-in, the Gemini key service, and the BYOK LLM proxy wiring
  - **Auth-model update (Google + Gemini BYOK):** reopened. The OpenAI OAuth/OIDC boundary and the plan-usage token path are replaced: Google sign-in establishes identity only, every Google token is discarded, and generation uses each Learner's own encrypted Gemini_API_Key. The existing app-session module, encrypted auth-start cookie, and callback HTML bridge are reused.
  - [x] 8.1 Implement the Google OAuth/OIDC boundary and Auth Service
    - **Delta from the OpenAI implementation:** switch endpoints, scopes, and client authentication to Google; add the nonce and the consumed-state registry; validate the Google ID token; drop refresh-token, plan-usage-token, and `OAuthCredential` handling; add logout revocation.
    - `app/auth/oauth.py`: Google endpoint constants (authorization `https://accounts.google.com/o/oauth2/v2/auth`, token `https://oauth2.googleapis.com/token`, JWKS `https://www.googleapis.com/oauth2/v3/certs`); per login generate a fresh `state` (32 random bytes, base64url), a fresh `nonce`, and a PKCE verifier (43–128 chars) with its S256 challenge; build the authorization URL with `client_id`, `redirect_uri`, `response_type=code`, `scope=openid email profile`, `state`, `nonce`, `code_challenge`, `code_challenge_method=S256`, `prompt=select_account`, and **no `access_type=offline`**; put state, nonce, verifier, and `expires_at = now + 10 min` into the existing encrypted HttpOnly SameSite=Lax auth-start cookie (path `/api/auth`, `Max-Age=600`, `Secure` when the redirect URI is HTTPS); exchange the code exactly once with `grant_type=authorization_code`, `code`, `code_verifier`, `redirect_uri`, `client_id`, `client_secret`, and a 30 s timeout (timeout, transport error, non-2xx, or no `id_token` → `TokenExchangeError`); fetch JWKS cached per `Cache-Control: max-age` with one refetch on an unknown `kid`. Remove dynamic client registration, the plan-usage scopes and resource, `ext_agent_host_id`, and token refresh
    - `app/auth/oidc.py`: replace the OpenAI validator with the pure `validate_google_id_token(id_token, jwks, *, client_id, expected_nonce, now, leeway_seconds=300)` that runs the six checks (`iss` ∈ {`https://accounts.google.com`, `accounts.google.com`}; `aud` == client ID; `exp` > now − 300 s; RS256 signature against the JWKS key matching `kid`, `alg` must be RS256; constant-time `nonce` match; `email_verified` true), raises `IDTokenValidationError` on any failure, and returns `GoogleIdentity` with the issuer normalized to `https://accounts.google.com`
    - Consumed-state registry: record a SHA-256 hash of each accepted state with its expiry (in-process, TTL-based) so a replayed cookie within its 10 minutes is rejected
    - `AuthService` (`build_authorization_url`, `handle_callback`, `logout`): the callback always deletes the auth-start cookie; a provider `error` (e.g. `access_denied`) → `AuthorizationDeniedError`; state missing, mismatched (constant-time compare), expired, or already used → `AuthStateError` with **no token exchange**; otherwise mark the state used, then exchange; an ID-token or JWKS failure → `AccountVerificationError` with no account created or modified; hold the Google `access_token`, `refresh_token`, and raw token response only in local variables and never persist or log them; upsert the account by `(oidc_issuer, oidc_subject)` with the normalized issuer, refreshing `email`/`name` without using them as the key. Remove `get_valid_access_token` and the encrypted OAuth token storage
    - `app/auth/session.py`: issue the App_Session HS256 JWT (`SESSION_SECRET`) with a `sid` claim and a 12 h TTL; `logout(session_id, expires_at)` inserts the `sid` into `revoked_app_sessions` until the session's `exp`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12, 9.13_

  - [x] 8.2 Implement API dependencies for current user, DB session, and service wiring
    - **Auth-model update (Google + Gemini BYOK):** reopened. Wiring changes from the Auth Service access-token provider to GeminiKeyService + GeminiAdapter. Implement after 8.5 (see the dependency graph).
    - In `app/api/deps.py`: `get_current_user` rejects missing, invalid, expired, and revoked (`revoked_app_sessions`) sessions with the same `401` and no data; provide a DB session; provide `GeminiKeyService` (DB session, Fernet cipher from `TOKEN_ENCRYPTION_KEY`, shared `httpx.AsyncClient`); provide `SentenceGenerationService` as `GeminiAdapter` with its `ApiKeyProvider` bound to `GeminiKeyService.get_api_key` and the model from `GEMINI_MODEL`; give ReviewService access to the key-presence check; remove the `OpenAIAdapter` / `get_valid_access_token` wiring. **No developer-held key anywhere**
    - _Requirements: 9.13, 9.14, 10.1, 10.16, 10.17_

  - [ ]* 8.3 Write integration test for the authenticated BYOK LLM proxy boundary
    - Assert sentence routes require a valid, non-revoked app session; with two users holding different keys, the `x-goog-api-key` recorded by the mocked transport on each `generateContent` call is the calling Learner's own key; the key is **never returned to the client**; and `Settings` exposes no Gemini key field
    - _Requirements: 9.14, 10.12, 10.16, 10.17_

  - [ ]* 8.4 Write property test for Google ID-token validation
    - **Property 25: Google ID token validation accepts only tokens passing all six checks**
    - **Validates: Requirements 9.8, 9.10**
    - Generate claim sets with independently toggled defects (wrong `iss`, wrong `aud`, `exp` around the 300 s leeway boundary, wrong signing key or `alg`, wrong `nonce`, `email_verified` false or missing) and both issuer spellings, signed with locally generated RSA keys served as a JWKS; assert `validate_google_id_token` returns an identity iff all six checks pass, otherwise raises `IDTokenValidationError`, and the returned issuer is always `https://accounts.google.com`

  - [x] 8.5 Implement the GeminiKeyService
    - `app/auth/secrets.py`: the Fernet `SecretCipher` keyed by `TOKEN_ENCRYPTION_KEY` (reuse the existing auth-start cookie cipher), `normalize_gemini_key` (trim leading/trailing whitespace, then raise `KeyFormatError` if the trimmed value is empty, contains internal whitespace, contains a non-printable/control character, or has length < 20 or > 200 — format-agnostic so both `AIza…` and new `AQ.…` keys pass; real validity is decided by the Gemini verification request), `key_hint` (last 4 characters), and `redact(text, secret)` (replaces every occurrence with `[REDACTED]`)
    - Add the pure `classify_verification(status, body)` to `app/services/gemini_errors.py`: 200 → `VALID`; 400 with `API_KEY_INVALID`, 401, 403 → `INVALID`; 429 or `RESOURCE_EXHAUSTED` → `QUOTA_EXHAUSTED`; any other status, a timeout, or a transport failure (`None`) → `UNAVAILABLE`
    - `app/services/gemini_key_service.py` (`status`, `connect`, `remove`, `get_api_key`): `connect` normalizes first (a format failure stores nothing and sends no request); takes a per-user non-blocking lock and raises `KeyVerificationInProgressError` if one is already held; sends exactly one `GET https://generativelanguage.googleapis.com/v1beta/models?pageSize=1` with `x-goog-api-key` and a 10 s total timeout (no content generated); `VALID` → Fernet-encrypt and upsert `gemini_credentials` in one transaction, replacing any previous key, with `quota_exhausted_notice=false`; `QUOTA_EXHAUSTED` → store the same way with `quota_exhausted_notice=true`; `INVALID` → `KeyRejectedError`; `UNAVAILABLE` → `KeyVerificationUnavailableError`; nothing is written on any failure, so the previous key stays in use
    - `remove` deletes the row and returns the not-connected status; `get_api_key` decrypts on demand and raises `MissingGeminiKeyError` when no key is stored; `status` returns `{connected, key_hint}`
    - Secrecy: never log the key; re-raise httpx errors as the service's own exception types with fixed messages and `from None`; pass any logged provider text through `redact`
    - Create `app/schemas/gemini_key.py` (`GeminiKeyIn(api_key: SecretStr)` and the status/connect response models)
    - _Requirements: 10.5, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11, 10.12, 10.13, 10.14, 10.15, 10.16, 10.17_

  - [ ]* 8.6 Write property test for Gemini key format validation
    - **Property 21: Key format validation accepts exactly well-formed keys and stays offline otherwise**
    - **Validates: Requirements 10.5, 10.6**
    - Generate well-formed keys from the regex alphabet wrapped in random whitespace plus near-misses (38/40 chars, wrong prefix, out-of-alphabet characters, empty or whitespace-only strings); assert `normalize_gemini_key(s)` succeeds iff `s.strip()` fully matches and then returns `s.strip()`, and that on failure `connect` stores nothing and the `httpx.MockTransport` records zero requests

  - [ ]* 8.7 Write property test for key secrecy and the last-4 hint
    - **Property 22: Stored keys never leave the backend except as the last-4 hint**
    - **Validates: Requirements 10.11, 10.12, 10.13**
    - For any well-formed key `k` and every verification or generation outcome (valid, invalid, quota, timeout, transport error, 5xx, malformed body), assert no API response body, error message, or captured log record contains `k`, and that after a successful connect the `key_hint` from `PUT /api/gemini-key`, `GET /api/gemini-key`, and `GET /api/auth/me` equals `k[-4:]` (runs after 9.1 and 9.5)

  - [ ]* 8.8 Write property test for key encryption round trip
    - **Property 23: Key encryption round-trips and never stores plaintext**
    - **Validates: Requirements 10.11**
    - For any well-formed key `k`, assert `decrypt(encrypt(k)) == k`, `encrypt(k) != k`, and the persisted `api_key_encrypted` value does not contain `k` as a substring

  - [ ]* 8.9 Write property test for key verification outcome mapping
    - **Property 26: Key verification outcomes map to store-or-reject correctly**
    - **Validates: Requirements 10.8, 10.9, 10.10, 10.14**
    - Generate verification results (status codes with Google RPC error bodies, timeouts, transport errors), with and without a previously stored key; assert 200 → stored with `quota_exhausted_notice = false`; 429 or `RESOURCE_EXHAUSTED` → stored with `quota_exhausted_notice = true`; 400 `API_KEY_INVALID`, 401, 403 → `invalid_key`, nothing stored; anything else → `could_not_verify`, nothing stored; and the previous key is unchanged in every non-storing case

- [x] 9. Implement API routes (wire backend layers to HTTP)
  - [x] 9.1 Implement Google sign-in auth routes
    - **Auth-model update (Google + Gemini BYOK):** reopened. The OpenAI login/callback routes switch to Google, the existing HTML bridge stays, logout gains revocation, and `GET /api/auth/me` is new.
    - In `app/api/routes/auth.py`: `GET /api/auth/login` sets the encrypted auth-start cookie and redirects (302) to Google's authorization endpoint, or returns a configuration error page without redirecting when the client ID, client secret, or encryption key is missing (logged without secret values); `GET /api/auth/callback` runs `AuthService.handle_callback` and returns the existing HTML bridge, which stores the App_Session in the SPA and navigates to `/`, or carries a safe error code for each failure class (state failure 9.4, authorization denied 9.5, token-exchange failure 9.7, account verification failure 9.10), creating no App_Session on failure; no Google token appears in any response header, body, or log
    - `POST /api/auth/logout` requires the bearer session and revokes its `sid` until `exp`
    - `GET /api/auth/me` returns `{email, name, gemini_key: {connected, key_hint}}` using `GeminiKeyService.status`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12, 9.13, 9.14, 10.12_

  - [x] 9.2 Implement word routes
    - Create `app/api/routes/words.py` with `POST /api/words` (400 per-field errors, 409 duplicate) and `GET /api/words`; register the router
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 9.3 Implement review routes
    - **Auth-model update (Google + Gemini BYOK):** reopened. The plan-usage error mapping is replaced by the Gemini BYOK error codes, and session start now checks for a stored key first.
    - Add the key-presence check to `ReviewService.start_session` before due selection (raises `MissingGeminiKeyError`); `POST /api/review/sessions` maps it to `409 {code: "gemini_key_required"}`, otherwise returns the due words or no-due result as before; word routes stay usable without a key
    - In `app/api/routes/review.py`, the sentence route `POST /api/review/sessions/:id/words/:wordId/sentence` maps `MissingGeminiKeyError` → 409 `gemini_key_required`, `InvalidGeminiKeyError` → 422 `invalid_gemini_key`, `QuotaExceededError` → 429 `gemini_quota_exceeded`, `GenerationTimeout` → 502 `generation_timeout`, and `GenerationError` → 502 `generation_failed`; each body is `{code, message}` with a fixed, key-free message, and the Target_Word stays unrated in the session; remove the `PlanIneligibleError` / `UsageCapExceededError` mapping
    - Keep the existing 3/4 handling and `POST /api/review/sessions/:id/words/:wordId/rating` behavior (reveal-gating 6.5, invalid-rating rejection 8.6, schedule + advance/summarize)
    - _Requirements: 3.1, 3.5, 4.1, 4.2, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 4.11, 6.5, 7.4, 7.5, 8.1, 8.3, 8.4, 8.5, 8.6, 10.1, 10.2, 10.15_

  - [ ]* 9.4 Write integration test for the end-to-end session flow
    - Against a test database with a mocked provider: start session → generate sentence → reveal → rate → advance → summary; assert auth-protected access
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [x] 9.5 Implement Gemini key routes and the key-safe validation handler
    - Create `app/api/routes/gemini_key.py`: `GET /api/gemini-key` → `{connected, key_hint}`; `PUT /api/gemini-key` with body `GeminiKeyIn` → `200 {connected: true, key_hint, quota_exhausted_notice}`, `400 {code: "invalid_key_format"}`, `400 {code: "invalid_key"}`, `409 {code: "key_verification_in_progress"}`, `503 {code: "could_not_verify"}`; `DELETE /api/gemini-key` → `200 {connected: false, key_hint: null}`; all three require a valid, non-revoked App_Session and return `503` when `TOKEN_ENCRYPTION_KEY` is missing; register the router in `app/main.py`
    - In `app/main.py`, register a `RequestValidationError` handler that removes `input` and `ctx` from every error entry so a submitted key is never echoed; access logging records only method, path, and status
    - _Requirements: 9.14, 10.5, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11, 10.12, 10.13, 10.14, 10.15_

- [x] 10. Checkpoint - backend complete
  - Ensure all backend tests pass (now including Google sign-in, logout revocation, the Gemini key routes, and the BYOK Gemini proxy, all against mocked Google and Gemini transports), ask the user if questions arise.

- [x] 11. Frontend scaffolding and typed API client
  - [x] 11.1 Scaffold the Vite React + TypeScript app
    - Create `frontend/` with Vite + React + TS, Tailwind (or CSS modules), `package.json`, `index.html`, and `src/{components,views,api}/`
    - _Requirements: frontend foundation_

  - [x] 11.2 Implement the typed API client
    - **Auth-model update (Google + Gemini BYOK):** reopened. Extend the existing `request<T>()`-based client with the Google `/me` and Gemini key endpoints, and replace the plan-usage error states.
    - In `src/api/client.ts`: add the types `GeminiKeyStatus`, `Me`, `GeminiKeyConnectResult`, `GeminiKeyErrorCode` (`invalid_key_format`, `invalid_key`, `could_not_verify`, `key_verification_in_progress`), and `SentenceErrorCode` (`gemini_key_required`, `invalid_gemini_key`, `gemini_quota_exceeded`, `generation_timeout`, `generation_failed`); add `getMe`, `getGeminiKeyStatus`, `putGeminiKey`, and `deleteGeminiKey`; keep `logout`, `loginUrl()` (`/api/auth/login`, a full-page navigation), and the existing words, import, session start, sentence generation, and rating functions with the shared types (tokens, GeneratedSentence, Difficulty)
    - Map non-2xx responses to `ApiError { status, code, message }` with the new codes and treat any 401 as signed out; remove the plan-ineligible / usage-limit states; never log or persist a submitted key
    - _Requirements: 4.5, 4.7, 4.8, 4.9, 4.10, 4.11, 9.14, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11, 10.12, backend API contract_

  - [x] 11.3 Switch the Login view to "Google로 계속하기"
    - **Auth-model update (Google + Gemini BYOK):** reopened. Keep the existing polished prototype look and the callback bridge handling; only the provider, button, and messages change.
    - In `src/views/Login`: replace the "Continue with ChatGPT" control with a single "Google로 계속하기" button that navigates to `loginUrl()`; render the bridge error codes as "login could not be verified" (9.4), "sign-in was not completed" (9.5), "sign-in could not be completed" (9.7), and "account could not be verified" (9.10), each with a retry control that starts a new login; show the Login view after logout and on any 401; remove the plan-ineligible / usage-limit messages (generation errors now render in the Review view, task 11.4)
    - _Requirements: 9.1, 9.4, 9.5, 9.7, 9.10, 9.13, 9.14_

  - [x] 11.4 Build the GeminiKeySetup view (Key_Setup_Guide) and the Review view key states
    - Create `src/views/GeminiKeySetup` in the same visual style as the existing views, showing five numbered steps in order: (1) open the Google AI Studio API key page via a link to `https://aistudio.google.com/apikey` with `target="_blank" rel="noopener noreferrer"`; (2) sign in with a Google account; (3) create a key by choosing an existing Google Cloud project or creating a new one; (4) copy the key; (5) paste it into the input below
    - Show the four notices: creating a key is free and needs no payment card; the free tier is rate limited; the Gemini API terms require users to be 18 or older; users in the EEA, Switzerland, or the UK must use a paid (billing-enabled) key
    - Key input: `type="password"` with a show/hide toggle, `autocomplete="off"`, and `spellCheck={false}`; the key lives only in component state (never `localStorage`, `sessionStorage`, URLs, or console output) and is cleared after a successful connect; the "연결 확인" button calls `putGeminiKey`, and the button and input are disabled while verification is in flight
    - Result states: format error, invalid key, could-not-verify-now, connected with the Key_Hint, and connected with the quota-exhausted notice; when connected, show the status with the Key_Hint (e.g. "연결됨 ····abcd"), a replace action (the current key stays in use until a new one is stored), and a remove action (confirm, `deleteGeminiKey`, not-connected status); a "나중에" control returns to the Add Word view; the view can open in replace mode
    - App shell/header: after sign-in call `getMe`; show the Learner's name or email, a "Gemini 키 설정" entry that opens GeminiKeySetup at any time, and the logout control; open GeminiKeySetup automatically when `gemini_key.connected` is false right after sign-in
    - Review Session view (`src/views/ReviewSession`): `409 gemini_key_required` on session start opens the Key_Setup_Guide instead of starting the session; on sentence generation it shows "Gemini API 키를 설정하세요" with a control that opens the guide and re-requests the sentence for the same word after a key is connected; `422 invalid_gemini_key` shows the invalid-key message with a control that opens GeminiKeySetup in replace mode; `429 gemini_quota_exceeded` shows the quota-exhausted, wait-and-retry-later message with a retry control; the word stays unrated in every case
    - README run instructions: create the Google OAuth client in Google Cloud Console (application type *Web application*, authorized redirect URI `http://localhost:5173/api/auth/callback`, OAuth consent screen with only the `openid`, `email`, and `profile` scopes and the developer's Google accounts added as test users), copy the client ID and secret into `backend/.env`, generate `SESSION_SECRET` and a Fernet `TOKEN_ENCRYPTION_KEY`, and get a free Gemini API key at `https://aistudio.google.com/apikey` to paste into the app; note that `jvt.db` can be deleted to start fresh after the migration
    - _Requirements: 4.7, 4.8, 4.9, 10.1, 10.2, 10.3, 10.4, 10.10, 10.11, 10.12, 10.13, 10.14, 10.15_

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

- [x] 17. Implement Anki deck import
  - Two-step flow from the design's "Anki Import (Requirement 2)" section: upload → Import_Preview, then confirm → Import_Summary, plus cancel and a 30-minute expiry. All decisions are pure functions in `app/services/anki_import/`; only the upload reader, the package reader, and the final write touch I/O. No new table and no migration. There is no feature flag, and import needs an App_Session but no Gemini key.
  - [x] 17.1 Add dependencies and the pure import helpers (errors, models, file kind, cleanup, mapping)
    - In `backend/pyproject.toml` add `zstandard>=0.22` (for `collection.anki21b`) and `python-multipart>=0.0.18` (streaming multipart parser with the boundary DoS fix); do not rely on the stdlib `compression.zstd`
    - Create the `app/services/anki_import/` package with `__init__.py` and:
      - `errors.py`: `AnkiImportError(code, message)` and one subclass per code with a fixed English message (`UnsupportedColpkgError`, `FileTooLargeError`, `UnsupportedExtensionError`, `UnreadableFileError`, `UnsafePackageError`, `InvalidTextExportError`, `UnrecognizedFormatError`, `NoNotesError`, `InvalidNoteTypeError`, `InvalidMappingError` carrying per-role errors, `PreviewExpiredError`, `ImportInProgressError`, `ImportFailedError`); messages never echo file content, entry names, or SQLite error text
      - `models.py`: `FileKind`, `NoteType` (string id, name, field_names, note_count), `AnkiNote` (raw values), and `ParsedDeck` with `field_names(note_type_id)` and `notes(note_type_id)`
      - `file_kind.py` (pure): `extension_of`, `is_colpkg`, `file_kind_for` (`.apkg` → APKG; `.txt`/`.tsv` → TEXT; else `UnsupportedExtensionError`), all case-insensitive on the basename
      - `cleanup.py` (pure): `field_cleanup` with the `_SOUND`, `_BREAK_TAG`, `_ANY_TAG` regexes in glossary order (sound tags, break tags → space, other tags removed, `html.unescape`, `" ".join(s.split())`); `has_furigana`, `furigana_word`, `furigana_reading` using `_FURIGANA = ( ?)([^\s\[\]]+)\[([^\]]+)\]`; and `resolve_note(values, mapping) -> (word_form, reading, meaning)` with the 2.25 (a)/(b)/(c) reading precedence
      - `mapping.py` (pure): `Role`, `FieldMapping` (0-based indexes, `None` = "없음"/no field), the three role-name sets, `suggest_mapping` (name match on `strip().casefold()`, then positional defaults with lowest-free fallback in the order word, reading, meaning), `validate_mapping` (`missing`, `out_of_range`, `duplicate` recorded for every sharing role), and `choose_default_note_type` (sort by `(-note_count, name, id)`)
    - _Requirements: 2.1, 2.2, 2.3, 2.8, 2.18, 2.19, 2.21, 2.22, 2.23, 2.24, 2.25_

  - [ ]* 17.2 Write property test for Field_Cleanup
    - **Property 28: Field_Cleanup removes markup and reaches a normal form**
    - **Validates: Requirements 2.23**
    - Generate segment lists of plain text, break tags (`<br>`, `<br/>`, `</div>`, `<p class="x">`, `<LI>`), other tags, `[sound:…]` tags, entities, and Unicode whitespace; assert the whitespace normal form, idempotence when the cleaned value has no `<`, `&`, or `[sound:`, and the segment oracle

  - [ ]* 17.3 Write property test for furigana word and reading resolution
    - **Property 29: Furigana is stripped from the word form and the reading follows its precedence**
    - **Validates: Requirements 2.24, 2.25**
    - Generate well-formed plain and `base[reading]` segment sequences with delimiter spaces; assert `furigana_word` / `furigana_reading` equal the concatenation oracles, and that `resolve_note` follows the mapped-reading → furigana-reading → word-form precedence (including reading values that clean to empty)

  - [ ]* 17.4 Write property test for the suggested Field_Mapping
    - **Property 30: Suggested Field_Mapping follows the name and position rules and never reuses a field**
    - **Validates: Requirements 2.18, 2.19, 2.21**
    - Generate field-name lists of length 0–6 mixing role names (random case and surrounding whitespace) and noise names; compare against an oracle of Req 2.18–2.19, assert no index is reused, and assert `validate_mapping(suggest_mapping(names), n) == {}` when `n ≥ 3`, or `n = 2` with no reading-name match

  - [ ]* 17.5 Write property test for mapping validation
    - **Property 31: Mapping validation names exactly the invalid roles**
    - **Validates: Requirements 2.21, 2.22**
    - For `n` in `0..6` and each role in `{None, -1..7}`, assert the result is empty exactly for valid mappings and otherwise names exactly the missing, out-of-range, and index-sharing roles

  - [ ]* 17.6 Write property test for the default note type
    - **Property 32: Default note type has the most notes, ties broken by name**
    - **Validates: Requirements 2.8**
    - Generate non-empty note type lists with counts in `1..3` and names including Hangul, kana, and case variants; assert the chosen type has the maximum count and the smallest name in code point order among those

  - [x] 17.7 Implement the Anki plain-text reader and printer
    - Create `app/services/anki_import/text_reader.py` (pure) with `TextExport(headers, rows)`:
      - `parse_anki_text(data: bytes)` (Anki_Text_Parser): drop a leading UTF-8 BOM, strict UTF-8 decode; header phase for leading `#key:value` lines (key lowercased and stripped, CR dropped, lines without `:` and empty lines skipped, phase ends at the first non-empty non-`#` line); reject a `separator` header that is neither a tab character nor `tab` (case-insensitive) after stripping spaces; row state machine (TAB splits fields, LF or CRLF ends a row, lone CR is content, a field starting with `"` is quoted with `""` → `"`, trailing characters after a closing quote appended literally, empty lines ignored, a TAB-only line is a row of empty values); EOF inside a quoted field, bad UTF-8, or a bad separator raise `InvalidTextExportError`
      - `print_anki_text(rows)` (Anki_Text_Printer): TAB-joined values, LF row ends, a value quoted with doubled `"` when it contains TAB, LF, CR, or `"`, or starts with `#`; no header lines
      - `text_export_to_deck(export)`: width = max(column-name count, longest row); exclude the 1-based positions from `guid column`, `notetype column`, `deck column`, and `tags column` headers (non-integer or out-of-range values ignored); name each remaining position from `#columns:` or `"열 {p}"` using the original position; pad short rows with `""`; zero rows raise `NoNotesError`; return a TEXT `ParsedDeck` with no note types
    - _Requirements: 2.3, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16_

  - [ ]* 17.8 Write property test for the plain-text round trip
    - **Property 27: Anki plain-text round trip**
    - **Validates: Requirements 2.10, 2.16, 2.17**
    - Generate equal-length rows (at least one non-empty value each) from an alphabet weighted toward TAB, CR, LF, `"`, `#`, spaces, kana, and kanji, excluding a U+FEFF at the very start of the first value; encode as UTF-8 with or without a BOM and with LF or CRLF row ends; assert `parse_anki_text(encode(print_anki_text(rows))).rows == rows`

  - [ ]* 17.9 Write unit tests for text parsing errors and text deck shaping
    - Invalid UTF-8, `#separator:comma`, and an unclosed quote raise `InvalidTextExportError`; `#separator:Tab` and `#separator:\t` are accepted; a headers-only file raises `NoNotesError`
    - `#columns:` with fewer names than the widest row yields `열 N` names; the four exclusion headers drop their columns, including out-of-range and non-integer values; ragged rows are padded with empty values
    - _Requirements: 2.3, 2.11, 2.12, 2.13, 2.14, 2.15_

  - [x] 17.10 Implement the `.apkg` package reader and the programmatic test fixtures
    - Create `app/services/anki_import/apkg_reader.py` with `COLLECTION_PRIORITY`, `MAX_COLLECTION_BYTES = 200 MiB`, the pure `is_unsafe_entry_name` / `check_entry_names` (leading `/`, drive prefix such as `C:`, any `\`, or a `..` segment), and `read_apkg(path, workdir)`:
      - Zip safety: open with `zipfile.ZipFile` (`BadZipFile`, `OSError`, encrypted entry → `UnreadableFileError`); check every name from `infolist()` before opening any entry (`UnsafePackageError`)
      - Collection selection: first present of `collection.anki21b`, `collection.anki21`, `collection.anki2` (none → `UnrecognizedFormatError`); never open media entries or `meta`
      - Size cap: declared `file_size` over the cap, or for `anki21b` a zstd frame content size (from the first 18 bytes via `zstandard.frame_content_size`) over the cap → `UnsafePackageError`; stream-extract only the collection (through `ZstdDecompressor().stream_reader()` for `anki21b`) to `workdir/collection.sqlite` in 1 MiB chunks, counting bytes and deleting the partial file when the count passes the cap; `zlib.error`, `ZstdError`, and CRC failures → `UnreadableFileError`
      - Read-only SQLite as data only: `file:…?mode=ro&immutable=1` URI, never `enable_load_extension`, `PRAGMA query_only = ON`, `PRAGMA trusted_schema = OFF`, an authorizer allowing only `SQLITE_SELECT`, `SQLITE_READ`, and `SQLITE_FUNCTION`, and an app-registered `unicase` collation (Python `casefold` comparison); `sqlite3.DatabaseError` → `UnreadableFileError`
      - Schema detection from `sqlite_master` (`notes` table required; `notetypes` + `fields` → new schema; else `col` → legacy; else `UnrecognizedFormatError`); note types from `notetypes`/`fields ORDER BY ntid, ord` or from `col.models` JSON with fields sorted by `ord` (bad JSON or shape → `UnreadableFileError`); ids kept as strings
      - Notes via `SELECT id, mid, flds FROM notes ORDER BY id`, `flds` split on `\x1f` and padded/truncated to the note type's field count; notes with an unknown `mid` ignored; zero notes → `NoNotesError`; list only note types with at least one note, with counts; close the connection in `finally`; re-raise every library exception `from None`
    - Create `backend/tests/fixtures/anki_builders.py` that builds every fixture in code (no committed binaries): `build_legacy_apkg` (schema-11 `col` + `notes`, `media` = `{}`, optional extra entries and collection name), `build_anki21b_apkg` (`notetypes`/`fields` with `COLLATE unicase` registered on the builder connection, `notes`, zstd-compressed `collection.anki21b`, plus a legacy `collection.anki2` stub holding a different single note), and `build_text_export(rows, headers)` built on `print_anki_text`
    - _Requirements: 2.3, 2.5, 2.7, 2.8, 2.30, 2.39, 2.40, 2.41_

  - [ ]* 17.11 Write property test for unsafe package entry names
    - **Property 33: Packages with an unsafe entry name are rejected before anything is extracted**
    - **Validates: Requirements 2.41**
    - Build zips in memory with a valid collection, random safe names (`0`, `media`, `a/b.txt`), and at least one unsafe name (`/etc/x`, `C:x`, `a/../b`, `..`, `a\\b`); assert `UnsafePackageError`, no entry opened (spy on `ZipFile.open`), and an empty workdir; also assert `is_unsafe_entry_name` agrees with the Req 2.41 definition for any string

  - [ ]* 17.12 Write unit tests for the package reader
    - Collection priority: all seven presence combinations of the three collection names, each with distinct notes, read from the expected file
    - Size cap: a declared size of 200 MB + 1 and a zstd frame header advertising more than 200 MB raise `UnsafePackageError`; a lying size header with a small injected cap aborts and deletes the partial file
    - Reader details: non-zip and non-SQLite inputs raise `UnreadableFileError`; a zip with no collection raises `UnrecognizedFormatError`; a collection with zero notes raises `NoNotesError`; out-of-order note ids come back ascending; a note type with zero notes is not listed; field order follows `ord`; unknown-`mid` notes are ignored; media entries are never opened; the connection rejects a write and the authorizer denies a non-SELECT statement; a collection containing a trigger and a view parses and executes neither
    - _Requirements: 2.3, 2.5, 2.7, 2.8, 2.30, 2.39, 2.40, 2.41_

  - [x] 17.13 Implement the streaming upload reader and the preview store
    - Create `app/api/upload.py` with `MAX_UPLOAD_BYTES = 52_428_800`, `MULTIPART_OVERHEAD_BYTES = 1_048_576`, `ReceivedFile(filename, path, size)`, and `receive_single_file(request, workdir)` built on python-multipart's callback `MultipartParser` over `request.stream()`: require `multipart/form-data` with a boundary (else `UnreadableFileError`); keep only the basename of the client file name (split on `/` and `\`); on the `file` part headers raise `UnsupportedColpkgError` before any body byte is stored; append file bytes to `workdir/upload.bin` and raise `FileTooLargeError` as soon as the count passes the limit; discard other parts but cap the total body at limit + overhead; no `file` part → `UnreadableFileError`; never use `Content-Length` for a decision
    - Create `app/services/anki_import/preview_store.py` with `PREVIEW_TTL = 30 min`, `TEMP_PREFIX = "jvt-anki-"`, `PreviewState`, `PreviewRecord` (id from `secrets.token_urlsafe(32)`, owner `user_id`, file name, created/expires, state, parsed deck, selected note type), and `PreviewStore` with an injectable clock:
      - `new_workdir` (`tempfile.mkdtemp(prefix=TEMP_PREFIX)`) and `discard_workdir` (`shutil.rmtree(..., ignore_errors=True)`)
      - `add` cancels the same account's earlier `PENDING` records (supersede), never touching `IMPORTING` ones
      - `get_pending` raises `PreviewExpiredError` for unknown, foreign, expired (`now ≥ expires_at`), or non-`PENDING` previews alike; `select_note_type` raises `InvalidNoteTypeError` for an unlisted id
      - `begin_import` context manager: per-account `threading.Lock` with `acquire(blocking=False)` (`ImportInProgressError` and the record stays `PENDING`), re-check `PENDING` and unexpired under the lock, set `IMPORTING`, remove the record on success or failure, release the lock in `finally`
      - `cancel` is owner-only and idempotent; `sweep()` runs under the store lock at the start of every method and drops expired `PENDING` records; `remove_stale_workdirs()` deletes `jvt-anki-*` directories older than the TTL
    - _Requirements: 2.2, 2.3, 2.35, 2.36, 2.37, 2.40, 2.42_

  - [ ]* 17.14 Write unit tests for the upload reader and the preview store
    - Upload reader: a `.COLPKG` part is rejected with fewer than 1 MiB of body stored; a body over 52,428,800 file bytes stops with `FileTooLargeError` and the partial file is deleted by the caller's cleanup; a missing `file` part and a non-multipart content type raise `UnreadableFileError`
    - Preview store with a fake clock: `get_pending` succeeds at 29:59 and fails at 30:00; double confirm, confirm after cancel, confirm of a superseded preview, and another user's confirm each get `PreviewExpiredError`; `cancel` by another user leaves the preview confirmable by its owner; with the account lock held from another thread, `begin_import` raises `ImportInProgressError` and the preview stays `PENDING`; `remove_stale_workdirs` removes old `jvt-anki-*` directories only
    - _Requirements: 2.2, 2.3, 2.35, 2.36, 2.37, 2.40, 2.42_

  - [x] 17.15 Implement the bulk word methods, the import schemas, and the import service
    - In `app/services/word_service.py` add `existing_word_forms(user_id) -> set[str]` and `add_words_bulk(user_id, inputs, today, chunk_size=1000) -> int`; extract a shared private `_entry_values` helper also used by `add_word` (`next_review_date = today`, `ease_factor = 2.5`, `interval_days = 0`, `repetitions = 0`); insert with SQLAlchemy 2.0 bulk `insert(WordEntry)` in chunks and **do not commit**; `add_word` keeps its current behavior
    - Create `app/schemas/word_import.py` with `ImportNoteTypeOut`, `ImportFieldOut`, `FieldMappingOut`, `ImportPreviewOut` (note type ids as strings, fields by index, `samples` ≤ 5 cleaned rows, `expires_at` UTC), `ConfirmImportIn`, `SkippedNoteOut`, and `ImportSummaryOut`
    - Create `app/services/anki_import/import_service.py`:
      - `SkipReason`, `SkippedNote`, `ImportPlan`, and the pure `plan_import(notes, mapping, existing_forms)` that resolves each note with `resolve_note` in file order and applies the first matching rule: missing field → invalid length (50/100/500 code points, never truncating) → duplicate (existing forms or forms planned earlier in the same import) → `WordInput`
      - `AnkiImportService(db, store, words, today)` with `create_preview` (`file_kind_for`, then `read_apkg` or `text_export_to_deck(parse_anki_text(...))`, then `store.add`), `get_preview` (optional note-type switch via `select_note_type`), `confirm`, and `cancel`; a shared `build_preview(record)` uses the selected note type (default from `choose_default_note_type`) for `fields`, `suggested_mapping`, `note_count`, and the first 5 notes in file order after `field_cleanup`
      - `confirm` checks in order: `get_pending` → `preview_expired`; `note_type_id` (body or selected) must be listed for `.apkg` → `invalid_note_type`; `validate_mapping` → `invalid_mapping` with per-role errors and the preview left `PENDING`; then inside `begin_import` read `existing_word_forms`, run `plan_import` on the selected type's notes, call `add_words_bulk`, and `db.commit()` **once**; any exception calls `db.rollback()` and re-raises `ImportFailedError` `from None`; return the four counts and `skipped[:20]` with `word_form = None` for an empty word form
    - _Requirements: 2.4, 2.5, 2.8, 2.9, 2.21, 2.22, 2.23, 2.26, 2.27, 2.28, 2.29, 2.30, 2.31, 2.33, 2.34, 2.36, 2.37, 2.42_

  - [ ]* 17.16 Write property test for import accounting
    - **Property 5: Import accounting is exact**
    - **Validates: Requirements 2.9, 2.26, 2.27, 2.28, 2.29, 2.30, 2.33, 2.34**
    - Against in-memory SQLite, generate decks of up to 40 notes across one or more note types with empty, whitespace-only, over-length (51/101/501), repeated, and furigana-annotated values, plus existing collections overlapping the deck's word forms; assert the four counts sum to the included notes, each note gets the first applicable outcome, created entries have distinct word forms disjoint from existing ones, equal the resolved values, are due on the import date with default scheduling state and no review records, and `skipped` is the first `min(20, total)` skipped notes in file order with `word_form` null exactly when empty

  - [ ]* 17.17 Write property test for import atomicity
    - **Property 34: A failed import leaves the Word_Collection unchanged**
    - **Validates: Requirements 2.31**
    - With an injected `WordService` that fails after inserting `k` rows (chunk size 1) or a failing commit, for `k` from 0 to the number of rows to create, assert `confirm` raises `ImportFailedError`, returns no summary, leaves the user's `word_entries` rows identical to before, and removes the preview

  - [x] 17.18 Implement the import routes and wire them into the app
    - Create `app/api/routes/word_import.py` with four routes, all depending on `CurrentUser`:
      - `POST /api/words/import/preview`: declares no body parameter so the session is checked before any byte is read; creates a workdir, calls `receive_single_file`, runs `create_preview` with `run_in_threadpool`, returns `201 ImportPreviewOut`, and deletes the workdir in `finally` on every path
      - `GET /api/words/import/preview/{preview_id}?note_type_id=` → `200 ImportPreviewOut`
      - `POST /api/words/import/preview/{preview_id}/confirm`: parses the body inside the handler so a non-object body or non-integer role values become `422 invalid_mapping` with per-role errors → `200 ImportSummaryOut`
      - `DELETE /api/words/import/preview/{preview_id}` → `204`, idempotent and owner-only
    - One translator maps each `AnkiImportError` to `{"detail": {"code", "message"}}` with the design's status table (415 `unsupported_colpkg`, 413 `file_too_large`, 415 `unsupported_extension`, 400 `unreadable_file` / `unsafe_package` / `invalid_text_export` / `unrecognized_format` / `no_notes` / `invalid_note_type`, 422 `invalid_mapping` with `errors: [{field, reason, message}]`, 410 `preview_expired`, 409 `import_in_progress`, 500 `import_failed`); log import failures with the preview id and exception type only
    - In `app/api/deps.py` add a `get_preview_store` singleton and `get_anki_import_service`; in `app/main.py` register the router and add an app lifespan that calls `remove_stale_workdirs()` at startup
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.8, 2.9, 2.11, 2.22, 2.35, 2.36, 2.37, 2.38, 2.40, 2.41, 2.42_

  - [ ]* 17.19 Write example tests for malformed-file rejection and the ordered checks
    - Through the preview route: a 60 MB `deck.COLPKG` → `unsupported_colpkg` with under 1 MiB stored; a 60 MB `.zip` → `file_too_large`; a small `.zip` → `unsupported_extension`; a non-zip `.apkg` → `unreadable_file`; an unsafe entry name → `unsafe_package`; a zip with no collection → `unrecognized_format`; a collection with zero notes and a headers-only text file → `no_notes`; invalid UTF-8, a bad separator, and an unclosed quote → `invalid_text_export`
    - After every rejection: no preview is stored, `word_entries` is unchanged, the response does not echo file content, and the temp root holds no `jvt-anki-*` directory
    - _Requirements: 2.2, 2.3, 2.11, 2.40, 2.41_

  - [ ]* 17.20 Write integration tests for the import routes
    - Auth: all four routes return 401 with no data for missing, invalid, expired, and revoked sessions, and the upload body is not stored
    - End to end: upload an anki21b fixture with two note types, switch the note type, confirm, and check the summary and the stored words for the uploader only; repeat with a legacy anki2 fixture and a `.txt` export; another account's confirm and cancel cannot touch the preview; no Gemini key is stored in any of these tests
    - Lifecycle: preview creation, cancel, and an `invalid_mapping` confirm leave `word_entries` unchanged; a confirm while the account lock is held returns 409 `import_in_progress` and the preview stays confirmable; no `jvt-anki-*` directory remains after success, error, or cancel
    - Performance (marked `slow`): a generated 20,000-note anki21b deck and a 20,000-row text deck give a preview within 10 s and a confirm within 30 s
    - _Requirements: 2.4, 2.5, 2.6, 2.9, 2.26, 2.32, 2.35, 2.37, 2.38, 2.40, 2.42_

  - [x] 17.21 Implement the typed import client and client-side mapping validation
    - In `frontend/src/api/types.ts` add `ImportFileKind`, `ImportNoteType`, `ImportField`, `FieldMapping`, `ImportPreview`, `SkipReason`, the new `ImportSummary` (four counts and `skipped`), and `ImportErrorCode`; remove the old `ImportSummary`
    - In `frontend/src/api/client.ts` add `uploadForm<T>(path, formData, {onProgress, signal})` on `XMLHttpRequest` (same bearer token, `signOutLocally()` on 401, `upload.onprogress` fractions, abort via `signal`); export the shared `classify()` as `toApiError(status, payload)` for both helpers; add the `ImportErrorCode` values to `CODED_ERRORS` and `ApiErrorCode`; pass a coded detail's `errors` array through as `fieldErrors` so `invalid_mapping` arrives as `[{field, message}]`
    - In `frontend/src/api/endpoints.ts` replace `importWords(file)` with `createImportPreview(file, onProgress?, signal?)`, `getImportPreview(id, noteTypeId?)`, `confirmImport(id, body)`, and `cancelImportPreview(id)`; update `index.ts` exports and remove the `importWords` cases from `endpoints.test.ts` so the suite still compiles
    - Create `frontend/src/views/importMapping.ts` with `validateMappingClient(mapping, fieldCount)` using the same rules as the server's `validate_mapping`
    - _Requirements: 2.1, 2.4, 2.5, 2.8, 2.9, 2.20, 2.22, 2.33, 2.34, 2.35_

  - [ ]* 17.22 Write client tests for the import endpoints and mapping validation
    - `endpoints.test.ts`: the four new functions call the right method and path; `uploadForm` reports progress, classifies each import error code, passes `invalid_mapping` errors through as `fieldErrors`, and signs out on 401
    - `importMapping.test.ts`: `validateMappingClient` matches a shared table of server `validate_mapping` cases (missing, out of range, duplicate on every sharing role)
    - _Requirements: 2.20, 2.22, 2.38_

  - [x] 17.23 Build the ImportDeck panel and the Add Word tabs
    - In `frontend/src/views/AddWord.tsx` add a two-tab switch at the top of the right-hand card, **"직접 입력"** (existing form) and **"Anki 덱 가져오기"** (ImportDeck); the left intro column copy changes per tab; no feature flag and no Gemini key requirement
    - Create `frontend/src/views/ImportDeck.tsx` as the state machine `idle → uploading → preview → confirming → summary`, styled with the existing washi/ink/forest/vermilion tokens:
      - Idle: dashed drop zone with a vermilion "파일 선택" button over a hidden `<input type="file" accept=".apkg,.txt,.tsv">`, single-file drag and drop, the accepted extensions and 50 MB limit, and a collapsible "Anki에서 내보내는 방법" help box (파일(File) → 내보내기(Export…), "Anki Deck Package (.apkg)" or "Notes in Plain Text (.txt)", choose the deck, export; review history and media are not imported; `.colpkg` is not supported)
      - Client prechecks: a `.colpkg` shows the Req 2.2 message and a file over 52,428,800 bytes shows the size message, both without uploading; other extensions upload so the server decides
      - Uploading: forest progress bar with a percentage from `onProgress`, a cancel control that aborts, and "덱을 읽는 중…" at 100 %
      - Preview: file name and "노트 N개"; for `.apkg` a "노트 유형" select (`name (N개)`) that calls `getImportPreview` and replaces fields, mapping, samples, and count; selects **단어 (필수)**, **읽기 (선택)** with "없음" first, and **뜻 (필수)** starting at `suggested_mapping` (placeholder "필드를 선택하세요" for null); a sample table with role badges and two-line clamping with `title`; inline `validateMappingClient` errors under each select (a client-invalid mapping is not sent) and server `invalid_mapping` errors in the same places; a "N개 노트 가져오기" confirm button disabled while confirming and a "취소" button calling `cancelImportPreview`; a best-effort cancel when leaving the view with a pending preview
      - Summary: "단어 N개를 추가했어요", chips "빈 항목 N", "중복 N", "길이 초과 N", the first 20 skipped notes with "(빈 단어)" for a null word form and the reason label, the "건너뛴 노트 중 처음 20개만 보여요" note when more than 20 were skipped, and "다른 덱 가져오기" / "복습하러 가기" buttons
      - Errors: each upload error code shows its Korean message and returns to idle; `invalid_mapping` and `import_in_progress` stay on the preview; `preview_expired` and `import_failed` show their messages and return to idle
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.8, 2.9, 2.11, 2.20, 2.22, 2.30, 2.31, 2.33, 2.34, 2.35, 2.36, 2.41, 2.42_

  - [ ]* 17.24 Write UI tests for the Add Word tabs and the ImportDeck panel
    - The tabs switch between the form and the import panel; the file input accepts `.apkg,.txt,.tsv`; a dropped `.colpkg` or a file over 50 MB shows its message without calling `createImportPreview`; upload progress renders from a mocked `onProgress`
    - The preview renders the note-type select (`.apkg` only), the three selects with "없음" first in reading, the sample table with role badges, and inline missing/duplicate errors; a server `invalid_mapping` places errors under the matching selects
    - The summary shows the four counts and the skipped list with "(빈 단어)" and the "처음 20개" note; `preview_expired` and `import_failed` return to the drop zone; `import_in_progress` stays on the preview; cancel calls `cancelImportPreview`
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.8, 2.9, 2.20, 2.22, 2.31, 2.33, 2.34, 2.35, 2.36, 2.42_

  - [x] 17.25 Add the "Anki 덱 가져오기" section to the root README
    - In the root `README.md` add an "Anki 덱 가져오기" usage section: how to export from Anki (파일(File) → 내보내기(Export…), "Anki Deck Package (.apkg)" or "Notes in Plain Text (.txt)"), the accepted extensions and the 50 MB limit, that `.colpkg` is not supported, the preview and field-mapping step, the skip reasons in the summary, that review history and media are not imported, and that a preview expires after 30 minutes
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.20, 2.30, 2.33, 2.36_

- [x] 18. Checkpoint - Anki import complete
  - Run the full backend suite (`pytest`, including the `slow` import performance tests), the frontend tests (`npx vitest --run`), and the frontend build (`npm run build`); ensure all tests pass, ask the user if questions arise.

- [x] 19. Implement per-session study limits
  - Builds the design's Study_Day helper, the Study_Settings columns, capped review-first selection, the study-settings service and routes, and the 학습 설정 view (Requirements 1.5, 2.26, 8 revised, 11). Study settings need an App_Session and no Gemini key.
  - [x] 19.1 Add the pure Study_Day helper and use it for every "today"
    - In `backend/pyproject.toml` add `tzdata>=2024.1` so `ZoneInfo("Asia/Seoul")` resolves on hosts without a system time zone database
    - Create `app/services/study_day.py` with `STUDY_DAY_ZONE = ZoneInfo("Asia/Seoul")`, `STUDY_DAY_START = time(7, 0)`, `_utc_now()`, the pure `study_day(now_utc) -> date` (a naive datetime is read as UTC; convert to KST, subtract 7 h, take the date), and `current_study_day(clock=None)` that looks up `_utc_now` at call time so tests can monkeypatch `app.services.study_day._utc_now`
    - Replace each `date.today()`: `app/api/routes/review.py` `start_session` passes `current_study_day()`; `WordService.add_word` uses `today if today is not None else current_study_day()`; `AnkiImportService.__init__` defaults to `today: Callable[[], date] = current_study_day`; `add_words_bulk`, `_entry_values`, and `get_anki_import_service` stay unchanged
    - Add example tests: the five `study_day` boundary examples from the design; `add_word` without `today` and `AnkiImportService` without `today` give `next_review_date == 2 June` with `_utc_now` patched to 06:30 KST on 3 June; the review route passes `current_study_day()`
    - _Requirements: 1.5, 2.26, 8.1_

  - [ ]* 19.2 Write property test for the Study_Day boundary
    - **Property 35: Study_Day follows the 07:00 KST boundary**
    - **Validates: Requirements 1.5, 2.26, 8.1**
    - Generate naive and aware datetimes (UTC, Asia/Seoul, fixed offsets −12 h to +14 h) plus instants within ±2 s of 22:00:00 UTC and 15:00:00 UTC; assert `study_day(t) == (u + timedelta(hours=2)).date()` for `u` = `t` in UTC, naive equals `replace(tzinfo=UTC)`, equal instants in different zones agree, and `current_study_day(clock) == study_day(clock())`

  - [x] 19.3 Add the Study_Settings columns and migration
    - In `app/models/user.py` add `DEFAULT_NEW_WORD_LIMIT = 50`, `DEFAULT_REVIEW_WORD_LIMIT = 100`, `STUDY_LIMIT_MIN = 0`, `STUDY_LIMIT_MAX = 1000`, and the NOT NULL integer columns `new_word_limit` and `review_word_limit` with both ORM `default` and `server_default`
    - Create `alembic/versions/c7a1e9f4b2d8_add_study_settings.py` (`revision = 'c7a1e9f4b2d8'`, `down_revision = 'd2f7e806032a'`) using `op.batch_alter_table('users')`: upgrade adds the two columns with `server_default` `50` / `100`; downgrade drops only those two columns; no CHECK constraint
    - Update `tests/unit/test_models.py` (a new user row reads 50 / 100) and `tests/integration/test_migrations.py` (from `d2f7e806032a` with one user row, `upgrade head` gives 50 / 100 and NOT NULL columns; `downgrade d2f7e806032a` removes both columns with user and word rows intact)
    - _Requirements: 11.1, 11.2_

  - [x] 19.4 Implement capped, review-first selection in `ReviewService.start_session`
    - Keep the key gate as the first statement (`MissingGeminiKeyError` before any query) and the `start_session(user_id, day)` call shape; read `review_word_limit` / `new_word_limit` from the `User` row, falling back to the defaults when the row is missing
    - Classify with a correlated `EXISTS` on `ReviewRecord.word_id == WordEntry.id`: when `review_limit > 0`, Review_Words are reviewed words with `next_review_date <= day` ordered by `(next_review_date, created_at, id)` and limited to `review_limit`; when `new_limit > 0`, New_Words are words with no ReviewRecord, any `next_review_date`, ordered by `(created_at, id)` and limited to `new_limit`
    - Concatenate Review_Words then New_Words into the session word list with `session_date = day`; an empty list returns `NoDueWords` and registers no session; `reveal`, `record_rating`, and the summary stay unchanged
    - Update `tests/unit/test_review_service_start.py`: cases with future-dated unreviewed words now expect them in the New_Word group; add examples for a lapsed word (one ReviewRecord, `repetitions == 0`) with a future date (not selected) and with `next_review_date <= d` (selected as a Review_Word), limits 0 / 0 with a non-empty collection (`NoDueWords`), and no key with limits 0 / 0 (`MissingGeminiKeyError`)
    - _Requirements: 8.1, 8.2, 8.5, 10.1, 11.4, 11.6, 11.7_

  - [ ]* 19.5 Write property test for capped session selection
    - **Property 36: Session selection is capped, review-first, disjoint, and deterministic**
    - **Validates: Requirements 8.1, 8.2, 8.5, 11.4, 11.6, 11.7**
    - Against in-memory SQLite, generate 0–25 words for the Learner plus 0–5 for a second Learner, `next_review_date` within ±10 days of `d`, `created_at` from a few distinct seconds, 0–3 ReviewRecords per word, and limits from `{0, 1, 2, 3, collection size, 1000}` stored on the User row; compare `start_session(user_id, d)` with the sort-and-slice oracle `R + N`, assert `NoDueWords` exactly when `R + N` is empty, no repeated or foreign ids, and the same id list on a second call

  - [x] 19.6 Implement `StudySettingsService` and the study-settings schemas
    - Create `app/schemas/study_settings.py` with `StudyLimit = Annotated[StrictInt, Field(ge=STUDY_LIMIT_MIN, le=STUDY_LIMIT_MAX)]`, `StudySettingsIn` (both fields required, rejecting bool, float including `5.0`, numeric strings, and null) and `StudySettingsOut` (`from_attributes=True`)
    - Create `app/services/study_settings_service.py` with `StudySettingsService(db)`: `get(user_id)` loads the `User` and returns `StudySettingsOut`; `update(user_id, data: StudySettingsIn)` sets both columns and commits once, rolling back and re-raising on a database error
    - _Requirements: 11.1, 11.3, 11.4, 11.5, 11.8_

  - [ ]* 19.7 Write property test for study settings validation
    - **Property 37: Study settings are accepted exactly when both values are integers from 0 to 1000**
    - **Validates: Requirements 11.3, 11.4, 11.5**
    - Through FastAPI's `TestClient` on in-memory SQLite with an overridden `CurrentUser` (runs after 19.8), generate PUT bodies: objects with each field drawn from integers in `-5..1005`, booleans, floats (including `float(n)`), numeric strings, `None`, or absent, plus non-object JSON and malformed bytes; assert `200` iff both fields are non-bool integers in `0..1000` and a following `GET` returns the submitted pair, otherwise `422 invalid_study_settings` with `errors` naming exactly the invalid or missing fields (both for malformed or non-object bodies) and a following `GET` returning the previous pair

  - [x] 19.8 Implement the study-settings routes and wiring
    - In `app/api/deps.py` add `get_study_settings_service(db) -> StudySettingsService` and `StudySettingsServiceDep`
    - Create `app/api/routes/study_settings.py`: `GET` returns `service.get(user.id)`; `PUT` is `async def put_study_settings(request: Request, user: CurrentUser, service: StudySettingsServiceDep)` and parses the body by hand (malformed JSON or a non-object → errors for both fields; `StudySettingsIn.model_validate` failure → one entry per invalid field keyed by `loc[0]`, de-duplicated, ordered `new_word_limit`, `review_word_limit`), returning `422 {"detail": {"code": "invalid_study_settings", "message": "Study limits must be whole numbers from 0 to 1000.", "errors": [{field, message: "Must be a whole number from 0 to 1000."}]}}` with fixed messages, and on success `await run_in_threadpool(service.update, user.id, data)` → `200 StudySettingsOut`; both handlers use `user.id` only
    - In `app/main.py` register `app.include_router(study_settings.router, prefix="/api/study-settings", tags=["study-settings"])`
    - _Requirements: 11.3, 11.4, 11.5, 11.8, 11.9_

  - [ ]* 19.9 Write integration tests for the study-settings routes
    - `GET` returns 50 / 100 for a new user; Learner A saves `{1, 1}` and A's next `POST /api/review/sessions` returns at most one Review_Word and one New_Word, while Learner B on 50 / 100 gets the full selection and B's stored values are unchanged
    - `GET` and `PUT /api/study-settings` return 401 with no data for missing, invalid, expired, and revoked sessions (added to the existing auth-protected route list), a rejected PUT leaves the stored row unchanged, and neither route needs a Gemini key
    - _Requirements: 11.2, 11.3, 11.4, 11.8, 11.9_

  - [x] 19.10 Build the frontend study-settings client and the 학습 설정 view
    - In `frontend/src/api/types.ts` add `StudySettings` and `StudySettingsErrorCode = 'invalid_study_settings'`; in `client.ts` add `'invalid_study_settings'` to `CODED_ERRORS` (its type parameter list gains `StudySettingsErrorCode`) and to the `ApiErrorCode` union so a coded 422 is not classified as `invalid_rating`
    - In `frontend/src/api/endpoints.ts` add `STUDY_SETTINGS_PATH`, `getStudySettings()` (`GET`) and `putStudySettings(settings)` (`PUT` with a JSON body of both fields), both on `request<T>()`; export them from `index.ts`
    - Create `frontend/src/views/StudySettings.tsx`: a `surface-card` headed "학습 설정" with the design's description line; load with a spinner and a "설정을 불러오지 못했어요." + "다시 시도" failure state; inputs **신규 단어** and **복습 단어** (`type="number"`, `inputMode="numeric"`, `min={0}`, `max={1000}`, `step={1}`, raw string state) with the design's helper text; exported `validateStudyLimit(text)` (`^\d+$` and ≤ 1000) showing "0부터 1000 사이의 정수를 입력하세요" with `aria-invalid` / `aria-describedby` and disabling **저장**; save calls `putStudySettings` with numbers, is disabled while pending, shows the `role="status"` line "저장했어요. 다음 복습 세션부터 적용돼요." (cleared on the next edit), places server `fieldErrors` under the matching inputs, and shows "저장하지 못했어요. 잠시 후 다시 시도해 주세요." for other errors
    - In `frontend/src/App.tsx` extend `type View` with `'settings'`, add the "학습 설정" header button directly after the Gemini key entry (same classes and active state, glyph `設`, label from `lg`, `title`/`aria-label` "학습 설정", `data-testid="header-settings-entry"`, `aria-current="page"` when active, `setView('settings')`), and render `<StudySettings />` in `main` when `view === 'settings'`
    - _Requirements: 11.3, 11.4, 11.5_

  - [ ]* 19.11 Write UI tests for the 학습 설정 view
    - `StudySettings.test.tsx`: renders 신규 단어 / 복습 단어 with mocked 50 / 100; `validateStudyLimit` accepts "0", "1000", "50" and rejects "", "-1", "1001", "1.5", "1e2", "abc" with the message and a disabled 저장; 저장 sends numbers, is disabled while pending, and shows the success line; an `invalid_study_settings` error for `review_word_limit` shows under 복습 단어 only; a load failure shows the retry control
    - App shell: `header-settings-entry` sits after the Gemini key entry, opens the view, and has `aria-current="page"` while shown; `endpoints.test.ts` checks the GET and PUT calls; `client.test.ts` checks `classify(422, {code: "invalid_study_settings", ...})` gives that code with `fieldErrors`
    - _Requirements: 11.3, 11.4, 11.5_

- [x] 20. Checkpoint - study limits complete
  - Run the backend suite (`pytest`), the frontend tests (`npm test -- --run`), the type check (`npx tsc -b`), and the frontend build (`npm run build`); ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional tests and can be skipped for a faster MVP; core implementation tasks are never optional.
- The model MUST NOT implement sub-tasks postfixed with `*`.
- Each task references the specific requirements and/or correctness properties it implements for traceability.
- Property tests use Hypothesis with `@settings(max_examples=100)` and the per-property tag comment: `# Feature: japanese-vocab-trainer, Property {number}: {property_text}`.
- The pure `scheduler.py` and the Pydantic LLM-output validator are isolated units, making Properties 10–15 fast and deterministic; the same holds for the key helpers, the Gemini error classifiers, and `validate_google_id_token` (Properties 21–26). The provider is always mocked in tests and never called for real: all Google OAuth/OIDC and Gemini traffic goes through an injected `httpx.AsyncClient` on `httpx.MockTransport`.
- Checkpoints ensure incremental validation at the backend-complete and full-stack-wired boundaries.
- **Auth-model update (Google + Gemini BYOK):** sign-in is "Sign in with Google" (OAuth 2.0 Authorization Code + PKCE + OIDC with scopes `openid email profile`), used for identity only; Google access and refresh tokens are discarded and never persisted. Sentence generation calls the Gemini API from the backend with each Learner's own Gemini_API_Key (BYOK), connected through the in-app Key_Setup_Guide, stored Fernet-encrypted, and shown in the browser only as the last-4 Key_Hint. There is **no developer-held Gemini key and no OpenAI or ChatGPT dependency**. Tasks 1, 2.1, 2.2, 6.1, 6.2, 8.1, 8.2, 9.1, 9.3, 11.2, 11.3 and checkpoints 10 and 16 were reopened because the existing OpenAI "Sign in with ChatGPT" / plan-usage implementation no longer matches the design; 8.5, 9.5, and 11.4 are new; optional tests 6.6, 8.4 (repurposed), and 8.6–8.9 cover Properties 21–26.
- **Anki deck import (Requirement 2) is required.** Task 17 builds it: pure helpers (17.1), the text reader (17.7), the `.apkg` reader and code-built fixtures (17.10), the upload reader and preview store (17.13), the bulk word methods and import service (17.15), the routes (17.18), the typed client (17.21), the ImportDeck panel (17.23), and the README section (17.25). Its tests (17.2–17.6, 17.8, 17.9, 17.11, 17.12, 17.14, 17.16, 17.17, 17.19, 17.20, 17.22, 17.24) are optional like all other tests. Task 18 is its checkpoint.
- **Per-session study limits (Requirement 11, revised Requirement 8) are required.** Task 19 builds them: the Study_Day helper and its call sites (19.1), the `User` columns and migration `c7a1e9f4b2d8` (19.3), capped review-first selection (19.4), `StudySettingsService` and schemas (19.6), the `/api/study-settings` routes (19.8), and the frontend client, 학습 설정 view, and header button (19.10). Its tests (19.2, 19.5, 19.7, 19.9, 19.11) are optional like all other tests. Task 20 is its checkpoint.
- All 11 requirements and all 37 correctness properties are covered: Properties 1–4 (word service), 35–37 (study limits: 35 → 19.2, 36 → 19.5, 37 → 19.7), 5 and 27–34 (Anki import: 5 → 17.16, 27 → 17.8, 28 → 17.2, 29 → 17.3, 30 → 17.4, 31 → 17.5, 32 → 17.6, 33 → 17.11, 34 → 17.17), 6–9 (frontend render/lookup/reveal/rating), 10–12 (sentence generation), 13–16 (scheduler + persistence), 17–20 (review session), 21–23 and 26 (Gemini key service, tasks 8.6–8.9), 24 (Gemini error mapping, task 6.6), 25 (Google ID-token validation, task 8.4). Requirement 9 is covered by tasks 8.1, 8.2, 8.4, 9.1, and 11.3; Requirement 10 by tasks 8.5–8.9, 9.3 (10.1), 9.5, and 11.4; Requirement 11 by tasks 19.3, 19.4, 19.6, 19.8, and 19.10.
- Task 1 has no sub-tasks, so it is not listed in the dependency graph; complete it before wave 0. Completed checkpoints 10, 16, and 18 are not listed either; task 18 runs after wave 16. The open checkpoint 20 has its own wave (22), after all task 19 waves.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1", "3.1", "3.2", "4.1", "6.1"] },
    { "id": 1, "tasks": ["2.2", "3.3", "3.4", "4.2", "4.3", "4.4", "5.1", "6.2", "8.1"] },
    { "id": 2, "tasks": ["5.2", "5.3", "5.4", "6.3", "6.4", "6.5", "6.6", "7.1", "8.5"] },
    { "id": 3, "tasks": ["7.2", "8.2", "8.4", "8.6", "8.8", "8.9"] },
    { "id": 4, "tasks": ["7.3", "7.4", "7.5", "7.6", "7.7", "7.8"] },
    { "id": 5, "tasks": ["9.1", "9.2", "9.3", "9.5"] },
    { "id": 6, "tasks": ["8.3", "8.7", "9.4", "11.1"] },
    { "id": 7, "tasks": ["11.2"] },
    { "id": 8, "tasks": ["11.3", "12.1", "13.1", "14.1", "15.1"] },
    { "id": 9, "tasks": ["12.2", "13.2", "14.2", "15.2"] },
    { "id": 10, "tasks": ["11.4", "15.3"] },
    { "id": 11, "tasks": ["17.1"] },
    { "id": 12, "tasks": ["17.2", "17.3", "17.4", "17.5", "17.6", "17.7", "17.13", "17.21"] },
    { "id": 13, "tasks": ["17.8", "17.9", "17.10", "17.14", "17.22", "17.23"] },
    { "id": 14, "tasks": ["17.11", "17.12", "17.15", "17.24", "17.25"] },
    { "id": 15, "tasks": ["17.16", "17.17", "17.18"] },
    { "id": 16, "tasks": ["17.19", "17.20"] },
    { "id": 17, "tasks": ["19.1", "19.3"] },
    { "id": 18, "tasks": ["19.2", "19.4", "19.6"] },
    { "id": 19, "tasks": ["19.5", "19.8"] },
    { "id": 20, "tasks": ["19.7", "19.9", "19.10"] },
    { "id": 21, "tasks": ["19.11"] },
    { "id": 22, "tasks": ["20"] }
  ]
}
```

// Shared TypeScript types mirroring the backend Pydantic schemas.
// Keep these in sync with backend/app/schemas/*.

/** Ordered recall rating: very hard = 1 .. very easy = 4 (Req 6.2, 7.1). */
export enum Difficulty {
  VeryHard = 1,
  Hard = 2,
  Good = 3,
  VeryEasy = 4,
}

/** The four rating levels in presentation order (hardest first). */
export const DIFFICULTY_ORDER: Difficulty[] = [
  Difficulty.VeryHard,
  Difficulty.Hard,
  Difficulty.Good,
  Difficulty.VeryEasy,
]

export const DIFFICULTY_LABELS: Record<Difficulty, string> = {
  [Difficulty.VeryHard]: 'Very hard',
  [Difficulty.Hard]: 'Hard',
  [Difficulty.Good]: 'Good',
  [Difficulty.VeryEasy]: 'Very easy',
}

/** Add-word request body (Req 1.1–1.3). */
export interface WordInput {
  word_form: string
  reading: string
  meaning: string
}

/** Stored word record (Req 1). */
export interface WordEntryOut {
  id: string
  word_form: string
  reading: string
  meaning: string
  next_review_date: string // ISO date
  created_at: string // ISO datetime
}

// --- Anki deck import (Req 2) -------------------------------------------------

/** `.apkg` packages vs. Anki plain-text exports (`.txt` / `.tsv`). */
export type ImportFileKind = 'apkg' | 'text'

/** A note type listed in an `.apkg` preview; only types with notes appear (Req 2.8). */
export interface ImportNoteType {
  id: string
  name: string
  note_count: number
}

/** An available field of the selected note type, by 0-based index. */
export interface ImportField {
  index: number
  name: string
}

/** 0-based field indexes per role; `null` means no field ("없음") (Req 2.20, 2.21). */
export interface FieldMapping {
  word: number | null
  reading: number | null
  meaning: number | null
}

/** `POST /api/words/import/preview` and `GET .../preview/{id}` (Req 2.4, 2.5, 2.8). */
export interface ImportPreview {
  preview_id: string
  file_kind: ImportFileKind
  note_types: ImportNoteType[] // [] for .txt/.tsv
  selected_note_type_id: string | null // null for .txt/.tsv
  fields: ImportField[] // available fields of the selected note type
  suggested_mapping: FieldMapping
  note_count: number // included notes (selected note type only)
  samples: string[][] // <= 5 rows, cleaned, one value per field
  expires_at: string // ISO 8601 UTC
}

/** `POST .../preview/{id}/confirm` request body (Req 2.9, 2.20). */
export interface ConfirmImportBody {
  note_type_id?: string
  mapping: FieldMapping
}

/** Why a note was not imported (Req 2.26–2.29). */
export type SkipReason = 'missing_field' | 'duplicate' | 'invalid_length'

export interface SkippedNote {
  word_form: string | null // null when the cleaned word form is empty
  reason: SkipReason
}

/** Import_Summary returned by a successful confirm (Req 2.33, 2.34). */
export interface ImportSummary {
  created: number
  skipped_missing_field: number
  skipped_duplicate: number
  skipped_invalid_length: number
  skipped: SkippedNote[] // first 20, file order
}

/** Coded `{code, message}` failures from the import routes. */
export type ImportErrorCode =
  | 'unsupported_colpkg' // 415 (2.2)
  | 'file_too_large' // 413 (2.3)
  | 'unsupported_extension' // 415 (2.1, 2.3)
  | 'unreadable_file' // 400 (2.3)
  | 'unrecognized_format' // 400 (2.3)
  | 'no_notes' // 400 (2.3)
  | 'invalid_text_export' // 400 (2.11)
  | 'unsafe_package' // 400 (2.41)
  | 'invalid_note_type' // 400 (2.9)
  | 'invalid_mapping' // 422, per-role errors in fieldErrors (2.22)
  | 'preview_expired' // 410 (2.36, 2.37)
  | 'import_in_progress' // 409 (2.42)
  | 'import_failed' // 500 (2.31)

/** A single tokenized word within a generated sentence. */
export interface SentenceToken {
  surface: string
  reading: string
  meaning: string | null
  is_target: boolean
  contains_kanji: boolean
}

/** A validated generated example sentence (Req 4). */
export interface GeneratedSentence {
  text: string
  tokens: SentenceToken[]
}

/** A started review session with its due words (Req 8.1, 8.2). */
export interface ReviewSessionOut {
  session_id: string
  due_words: WordEntryOut[]
}

/** Marker returned when no words are due (Req 8.5). */
export interface NoDueWords {
  status: 'no_due_words'
  message: string
}

export function isNoDueWords(
  value: ReviewSessionOut | NoDueWords,
): value is NoDueWords {
  return (value as NoDueWords).status === 'no_due_words'
}

/** End-of-session summary (Req 8.4). */
export interface SessionSummary {
  total_reviewed: number
  counts_by_rating: Record<string, number>
}

/** Outcome of recording a rating (Req 7.4, 8.3, 8.4). */
export interface RatingResult {
  word_id: string
  next_review_date: string
  session_complete: boolean
  next_word: WordEntryOut | null
  summary: SessionSummary | null
}

/**
 * The app's first-party bearer session (App_Session) returned after the
 * Google sign-in callback. Google tokens never reach the browser (Req 9.12).
 */
export interface AppSessionResponse {
  access_token: string
  token_type: 'bearer'
}

/** Logout acknowledgement from the backend. */
export interface LogoutResponse {
  success: boolean
}

// --- Gemini API key (BYOK, Req 10) -------------------------------------------

/**
 * Browser-safe Gemini key status. Only the last-4 Key_Hint ever reaches the
 * client; the key itself never does (Req 10.12).
 */
export interface GeminiKeyStatus {
  connected: boolean
  key_hint: string | null
}

/** `GET /api/auth/me`: the signed-in Learner plus the key status. */
export interface Me {
  email: string
  name: string | null
  gemini_key: GeminiKeyStatus
}

/** Successful `PUT /api/gemini-key` (Req 10.10, 10.11). */
export interface GeminiKeyConnectResult {
  connected: true
  key_hint: string
  quota_exhausted_notice: boolean
}

/** `PUT /api/gemini-key` failures (Req 10.6–10.9). */
export type GeminiKeyErrorCode =
  | 'invalid_key_format' // 400: trimmed key does not match the format (10.6)
  | 'invalid_key' // 400: Gemini rejected the key (10.8)
  | 'could_not_verify' // 503: verification timed out / unavailable (10.9)
  | 'key_verification_in_progress' // 409: a verification is already running (10.7)

/** Server-side key storage failures on any key-dependent route. */
export type KeyStorageErrorCode =
  | 'key_storage_unavailable' // 503: TOKEN_ENCRYPTION_KEY missing/invalid
  | 'key_storage_failed' // 500: the key change could not be saved

// --- Study settings (Req 11) -------------------------------------------------

/** `GET` / `PUT /api/study-settings`: per-session limits, integers 0–1000. */
export interface StudySettings {
  new_word_limit: number
  review_word_limit: number
}

/** 422 from `PUT /api/study-settings`; per-field errors arrive in `fieldErrors` (11.5). */
export type StudySettingsErrorCode = 'invalid_study_settings'

// --- Sentence generation (Req 4) ---------------------------------------------

/** Sentence-generation (and session-start key check) failure codes. */
export type SentenceErrorCode =
  | 'gemini_key_required' // 409: no stored key (4.7, 10.1)
  | 'invalid_gemini_key' // 422: Gemini rejected the stored key (4.9)
  | 'gemini_quota_exceeded' // 429: Gemini_Quota exhausted (4.8)
  | 'generation_timeout' // 502 (4.5)
  | 'generation_failed' // 502 (4.10, 4.11)

/** Body shape of a typed `{code, message}` error under FastAPI's `detail`. */
export interface CodedErrorDetail<C extends string = string> {
  code: C
  message: string
}

export interface SentenceErrorResponse {
  detail: CodedErrorDetail<SentenceErrorCode>
}

// --- Sign-in (Req 9) ---------------------------------------------------------

/** Stable Google sign-in callback failure codes returned by the backend. */
export type AuthErrorCode =
  | 'login_not_verified' // state missing/mismatched/expired/used (9.4)
  | 'sign_in_not_completed' // Google error / cancel / deny (9.5)
  | 'sign_in_service_unreachable' // token exchange failed (9.7)
  | 'account_not_verified' // ID token / JWKS validation failed (9.10)
  | 'sign_in_unavailable' // server configuration or persistence failure

export interface AuthErrorResponse {
  code: AuthErrorCode
  message: string
  retry_url: string | null
}

// --- Errors ------------------------------------------------------------------

/** Discriminated error codes surfaced to the UI. */
export type ApiErrorCode =
  | 'validation' // per-field word validation (400)
  | 'duplicate' // duplicate word form (409)
  | 'unauthorized' // missing/invalid/expired/revoked App_Session (401)
  | SentenceErrorCode
  | GeminiKeyErrorCode
  | KeyStorageErrorCode
  | AuthErrorCode
  | ImportErrorCode
  | StudySettingsErrorCode
  | 'logout_failed' // 503: logout revocation could not be saved
  | 'not_revealed' // rating before reveal (Req 6.5)
  | 'invalid_rating' // out-of-set rating (Req 8.6)
  | 'scheduling_not_saved' // transactional write failed (Req 7.5)
  | 'not_found'
  | 'conflict'
  | 'unknown'

/** @deprecated Use {@link ApiErrorCode}. Kept for existing view code. */
export type ApiErrorKind = ApiErrorCode

export interface FieldError {
  field: string
  message: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: ApiErrorCode
  readonly fieldErrors: FieldError[]

  constructor(
    message: string,
    status: number,
    code: ApiErrorCode,
    fieldErrors: FieldError[] = [],
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.fieldErrors = fieldErrors
  }

  /** @deprecated Use {@link ApiError.code}. Kept for existing view code. */
  get kind(): ApiErrorCode {
    return this.code
  }
}

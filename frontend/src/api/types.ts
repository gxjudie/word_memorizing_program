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

export interface LoginResponse {
  access_token: string
  token_type: string
}

/** Discriminated error kinds surfaced to the UI. */
export type ApiErrorKind =
  | 'validation' // per-field word validation (400)
  | 'duplicate' // duplicate word form (409)
  | 'unauthorized' // 401
  | 'generation_timeout' // sentence generation timed out (Req 4.5)
  | 'generation_failed' // sentence generation exhausted retries (Req 4.6)
  | 'not_revealed' // rating before reveal (Req 6.5)
  | 'invalid_rating' // out-of-set rating (Req 8.6)
  | 'scheduling_not_saved' // transactional write failed (Req 7.5)
  | 'not_found'
  | 'conflict'
  | 'unknown'

export interface FieldError {
  field: string
  message: string
}

export class ApiError extends Error {
  readonly status: number
  readonly kind: ApiErrorKind
  readonly fieldErrors: FieldError[]

  constructor(
    message: string,
    status: number,
    kind: ApiErrorKind,
    fieldErrors: FieldError[] = [],
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.kind = kind
    this.fieldErrors = fieldErrors
  }
}

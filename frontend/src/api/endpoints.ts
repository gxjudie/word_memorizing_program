// Typed client functions for each backend endpoint.
import { request, setSessionToken, uploadForm } from './client'
import type {
  AppSessionResponse,
  ConfirmImportBody,
  Difficulty,
  GeminiKeyConnectResult,
  GeminiKeyStatus,
  GeneratedSentence,
  ImportPreview,
  ImportSummary,
  LogoutResponse,
  Me,
  NoDueWords,
  RatingResult,
  RevealedWord,
  ReviewSessionOut,
  StudySettings,
  WordEntryOut,
  WordInput,
} from './types'

// --- Auth -------------------------------------------------------------------

export const AUTH_LOGIN_PATH = '/api/auth/login'
export const AUTH_CALLBACK_PATH = '/api/auth/callback'

/** URL that starts "Sign in with Google"; use it for a full-page navigation. */
export function loginUrl(): string {
  return AUTH_LOGIN_PATH
}

/**
 * Start Google Authorization Code + PKCE sign-in through the backend.
 *
 * Navigation is required here rather than fetch: the browser must follow the
 * backend's 302 redirect to Google and later return through the callback
 * bridge, which stores the App_Session and navigates back to `/`.
 */
export function startLogin(
  navigate: (url: string) => void = (url) => window.location.assign(url),
): void {
  navigate(loginUrl())
}

/**
 * Fetch-based callback exchange (the backend answers non-navigation requests
 * with JSON). Stores only the app's own bearer session; Google tokens are
 * never returned by the backend.
 */
export async function handleAuthCallback(
  params: string | URLSearchParams = window.location.search,
): Promise<AppSessionResponse> {
  setSessionToken(null)
  const query =
    typeof params === 'string'
      ? new URLSearchParams(params.startsWith('?') ? params.slice(1) : params)
      : params
  const suffix = query.toString()
  const response = await request<AppSessionResponse>(
    `${AUTH_CALLBACK_PATH}${suffix ? `?${suffix}` : ''}`,
    { auth: false },
  )
  setSessionToken(response.access_token)
  return response
}

export async function logout(): Promise<LogoutResponse> {
  try {
    return await request<LogoutResponse>('/api/auth/logout', { method: 'POST' })
  } finally {
    setSessionToken(null)
  }
}

/** The signed-in Learner plus the browser-safe Gemini key status. */
export function getMe(): Promise<Me> {
  return request<Me>('/api/auth/me')
}

// --- Gemini API key (BYOK) ----------------------------------------------------

export const GEMINI_KEY_PATH = '/api/gemini-key'

export function getGeminiKeyStatus(): Promise<GeminiKeyStatus> {
  return request<GeminiKeyStatus>(GEMINI_KEY_PATH)
}

/**
 * Submit a Gemini_API_Key for verification and storage. The key is sent once
 * in the JSON body and is never logged, persisted, or put in a URL here.
 * Failures reject with an ApiError whose code is a GeminiKeyErrorCode
 * (or key_storage_unavailable / key_storage_failed).
 */
export function putGeminiKey(apiKey: string): Promise<GeminiKeyConnectResult> {
  return request<GeminiKeyConnectResult>(GEMINI_KEY_PATH, {
    method: 'PUT',
    body: { api_key: apiKey },
  })
}

export function deleteGeminiKey(): Promise<GeminiKeyStatus> {
  return request<GeminiKeyStatus>(GEMINI_KEY_PATH, { method: 'DELETE' })
}

// --- Study settings (Req 11) ---------------------------------------------------

export const STUDY_SETTINGS_PATH = '/api/study-settings'

/** The signed-in Learner's limits (50 / 100 until saved) (Req 11.2, 11.3). */
export function getStudySettings(): Promise<StudySettings> {
  return request<StudySettings>(STUDY_SETTINGS_PATH)
}

/**
 * Store both limits (Req 11.4). Invalid values reject with an ApiError whose
 * code is `invalid_study_settings` and whose `fieldErrors` name each field.
 */
export function putStudySettings(settings: StudySettings): Promise<StudySettings> {
  return request<StudySettings>(STUDY_SETTINGS_PATH, {
    method: 'PUT',
    body: {
      new_word_limit: settings.new_word_limit,
      review_word_limit: settings.review_word_limit,
    },
  })
}

// --- Words ------------------------------------------------------------------

export function addWord(input: WordInput): Promise<WordEntryOut> {
  return request<WordEntryOut>('/api/words', { method: 'POST', body: input })
}

export function listWords(): Promise<WordEntryOut[]> {
  return request<WordEntryOut[]>('/api/words')
}

/**
 * Replace a word's lexical fields. Its review schedule is preserved server-side,
 * so fixing a typo never moves the card. Rejects with `duplicate` (409) or
 * `not_found` (404); validation failures carry field-exact errors.
 */
export function updateWord(
  wordId: string,
  input: WordInput,
): Promise<WordEntryOut> {
  return request<WordEntryOut>(`/api/words/${encodeURIComponent(wordId)}`, {
    method: 'PATCH',
    body: input,
  })
}

/** Delete a word together with its review history and generated sentences. */
export function deleteWord(wordId: string): Promise<void> {
  return request<void>(`/api/words/${encodeURIComponent(wordId)}`, {
    method: 'DELETE',
  })
}

// --- Anki deck import (Req 2) -------------------------------------------------

export const IMPORT_PREVIEW_PATH = '/api/words/import/preview'

function importPreviewPath(id: string): string {
  return `${IMPORT_PREVIEW_PATH}/${encodeURIComponent(id)}`
}

/**
 * Upload one `.apkg` / `.txt` / `.tsv` file and get its Import_Preview
 * (201). Nothing is imported yet. Upload failures reject with an ApiError
 * whose code is an ImportErrorCode; aborting rejects with an `AbortError`.
 */
export function createImportPreview(
  file: File,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<ImportPreview> {
  const formData = new FormData()
  formData.append('file', file)
  return uploadForm<ImportPreview>(IMPORT_PREVIEW_PATH, formData, {
    onProgress,
    signal,
  })
}

/** Re-read a pending preview, optionally switching the `.apkg` note type (Req 2.8). */
export function getImportPreview(
  id: string,
  noteTypeId?: string,
): Promise<ImportPreview> {
  const query =
    noteTypeId !== undefined
      ? `?${new URLSearchParams({ note_type_id: noteTypeId }).toString()}`
      : ''
  return request<ImportPreview>(`${importPreviewPath(id)}${query}`)
}

/**
 * Import the previewed notes with the chosen Field_Mapping (Req 2.9).
 * `invalid_mapping` failures carry per-role errors in `fieldErrors`.
 */
export function confirmImport(
  id: string,
  body: ConfirmImportBody,
): Promise<ImportSummary> {
  return request<ImportSummary>(`${importPreviewPath(id)}/confirm`, {
    method: 'POST',
    body,
  })
}

/** Discard a pending preview (204, idempotent) (Req 2.35). */
export function cancelImportPreview(id: string): Promise<void> {
  return request<void>(importPreviewPath(id), { method: 'DELETE' })
}

// --- Review session ---------------------------------------------------------

/** Rejects with code `gemini_key_required` (409) when no key is stored. */
export function startSession(): Promise<ReviewSessionOut | NoDueWords> {
  return request<ReviewSessionOut | NoDueWords>('/api/review/sessions', {
    method: 'POST',
  })
}

/** Failures reject with an ApiError whose code is a SentenceErrorCode. */
export function generateSentence(
  sessionId: string,
  wordId: string,
): Promise<GeneratedSentence> {
  return request<GeneratedSentence>(
    `/api/review/sessions/${encodeURIComponent(sessionId)}/words/${encodeURIComponent(wordId)}/sentence`,
    { method: 'POST' },
  )
}

export function revealWord(
  sessionId: string,
  wordId: string,
): Promise<RevealedWord> {
  return request<RevealedWord>(
    `/api/review/sessions/${encodeURIComponent(sessionId)}/words/${encodeURIComponent(wordId)}/reveal`,
    { method: 'POST' },
  )
}

export function rateWord(
  sessionId: string,
  wordId: string,
  rating: Difficulty,
): Promise<RatingResult> {
  return request<RatingResult>(
    `/api/review/sessions/${encodeURIComponent(sessionId)}/words/${encodeURIComponent(wordId)}/rating`,
    { method: 'POST', body: { rating } },
  )
}

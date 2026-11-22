// Core typed HTTP client: first-party session storage, requests, and errors.
import {
  ApiError,
  type ApiErrorCode,
  type AuthErrorCode,
  type FieldError,
  type GeminiKeyErrorCode,
  type ImportErrorCode,
  type KeyStorageErrorCode,
  type SentenceErrorCode,
  type StudySettingsErrorCode,
} from './types'

// This key stores only the App_Session issued by our backend after Google
// ID-token validation. Google tokens and Gemini keys never reach the browser
// storage. The callback HTML bridge writes the same key.
const SESSION_TOKEN_KEY = 'jvt.token'

export function getSessionToken(): string | null {
  try {
    return localStorage.getItem(SESSION_TOKEN_KEY)
  } catch {
    return null
  }
}

export function setSessionToken(token: string | null): void {
  try {
    if (token === null) localStorage.removeItem(SESSION_TOKEN_KEY)
    else localStorage.setItem(SESSION_TOKEN_KEY, token)
  } catch {
    /* Ignore unavailable browser storage; the current page can still recover. */
  }
}

// --- Signed-out notification (Req 9.13, 9.14) --------------------------------

type SignedOutListener = () => void
const signedOutListeners = new Set<SignedOutListener>()

/**
 * Subscribe to "signed out" events, fired whenever any response is a 401.
 * The stored App_Session is already cleared when listeners run.
 * Returns an unsubscribe function.
 */
export function onSignedOut(listener: SignedOutListener): () => void {
  signedOutListeners.add(listener)
  return () => {
    signedOutListeners.delete(listener)
  }
}

function signOutLocally(): void {
  setSessionToken(null)
  for (const listener of [...signedOutListeners]) listener()
}

// --- Error classification -----------------------------------------------------

/** Codes the backend sends as `{code, message}`, mapped one-to-one. */
const CODED_ERRORS: ReadonlySet<string> = new Set<
  | SentenceErrorCode
  | GeminiKeyErrorCode
  | KeyStorageErrorCode
  | AuthErrorCode
  | ImportErrorCode
  | StudySettingsErrorCode
  | 'logout_failed'
>([
  // Study settings (Req 11.5); without this a coded 422 falls to invalid_rating
  'invalid_study_settings',
  // Anki deck import (Req 2.2, 2.3, 2.9, 2.11, 2.22, 2.31, 2.36, 2.41, 2.42)
  'unsupported_colpkg',
  'file_too_large',
  'unsupported_extension',
  'unreadable_file',
  'unrecognized_format',
  'no_notes',
  'invalid_text_export',
  'unsafe_package',
  'invalid_note_type',
  'invalid_mapping',
  'preview_expired',
  'import_in_progress',
  'import_failed',
  // Sentence generation / session start (Req 4.5, 4.7–4.11, 10.1)
  'gemini_key_required',
  'invalid_gemini_key',
  'gemini_quota_exceeded',
  'generation_timeout',
  'generation_failed',
  // Gemini key routes (Req 10.6–10.9)
  'invalid_key_format',
  'invalid_key',
  'could_not_verify',
  'key_verification_in_progress',
  'key_storage_unavailable',
  'key_storage_failed',
  // Google sign-in callback (Req 9.4, 9.5, 9.7, 9.10)
  'login_not_verified',
  'sign_in_not_completed',
  'sign_in_service_unreachable',
  'account_not_verified',
  'sign_in_unavailable',
  'logout_failed',
])

/** Keep only well-formed `{field, message}` entries of a coded detail's `errors`. */
function toFieldErrors(errors: unknown): FieldError[] {
  if (!Array.isArray(errors)) return []
  return errors.flatMap((entry: unknown) => {
    if (!entry || typeof entry !== 'object') return []
    const { field, message } = entry as { field?: unknown; message?: unknown }
    if (typeof field !== 'string') return []
    return [{ field, message: typeof message === 'string' ? message : '' }]
  })
}

/** Map an HTTP status + parsed detail into a discriminated ApiError code. */
function classify(status: number, detail: unknown): {
  code: ApiErrorCode
  message: string
  fieldErrors: FieldError[]
} {
  // Typed failures carry stable {code, message} data. A 422 with an object
  // detail is invalid_gemini_key; FastAPI's own 422 is a list (below).
  if (
    detail &&
    typeof detail === 'object' &&
    !Array.isArray(detail) &&
    'code' in detail
  ) {
    const code = String((detail as { code: unknown }).code)
    const message = String(
      (detail as { message?: unknown }).message ?? 'Request failed',
    )
    if (CODED_ERRORS.has(code)) {
      // A coded detail may carry per-role errors (invalid_mapping, Req 2.22).
      const fieldErrors = toFieldErrors((detail as { errors?: unknown }).errors)
      return { code: code as ApiErrorCode, message, fieldErrors }
    }
  }

  // Per-field word validation errors (Req 1.2, 1.3): {message, errors:[{field,message}]}.
  if (
    status === 400 &&
    detail &&
    typeof detail === 'object' &&
    Array.isArray((detail as { errors?: unknown }).errors)
  ) {
    const errors = (detail as { errors: FieldError[] }).errors
    const message = String(
      (detail as { message?: unknown }).message ?? 'Invalid input',
    )
    return { code: 'validation', message, fieldErrors: errors }
  }

  const text =
    typeof detail === 'string'
      ? detail
      : detail && typeof detail === 'object' && 'message' in detail
        ? String((detail as { message: unknown }).message)
        : null

  const message = text ?? `Request failed (${status})`

  switch (status) {
    case 401:
      return { code: 'unauthorized', message, fieldErrors: [] }
    case 409: {
      const code: ApiErrorCode = /revealed/i.test(message)
        ? 'not_revealed'
        : /already exists|duplicate/i.test(message)
          ? 'duplicate'
          : 'conflict'
      return { code, message, fieldErrors: [] }
    }
    case 404:
      return { code: 'not_found', message, fieldErrors: [] }
    case 422:
      // FastAPI request-validation errors (list detail), e.g. an out-of-set
      // rating rejected by RatingInput (Req 8.6).
      return { code: 'invalid_rating', message, fieldErrors: [] }
    case 500:
      return {
        code: /scheduling/i.test(message) ? 'scheduling_not_saved' : 'unknown',
        message,
        fieldErrors: [],
      }
    default:
      return { code: 'unknown', message, fieldErrors: [] }
  }
}

/**
 * Build the ApiError for a non-2xx response from its parsed payload. Shared
 * by `request<T>()` and `uploadForm<T>()` so both classify identically.
 */
export function toApiError(status: number, payload: unknown): ApiError {
  // Most FastAPI errors use {detail: ...}; auth callback/logout errors are top-level.
  const detail =
    payload && typeof payload === 'object' && 'detail' in payload
      ? (payload as { detail: unknown }).detail
      : payload
  const { code, message, fieldErrors } = classify(status, detail)
  return new ApiError(message, status, code, fieldErrors)
}

function parsePayload(raw: string): unknown {
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return raw
  }
}

// --- Request ------------------------------------------------------------------

export interface RequestOptions {
  method?: string
  /** JSON value serialized by the client. Never logged. */
  body?: unknown
  /** Multipart body; the browser supplies its boundary Content-Type. */
  formData?: FormData
  auth?: boolean
}

export async function request<T>(
  path: string,
  { method = 'GET', body, formData, auth = true }: RequestOptions = {},
): Promise<T> {
  if (body !== undefined && formData !== undefined) {
    throw new TypeError('A request cannot have both JSON and multipart bodies')
  }

  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = getSessionToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  let resp: Response
  try {
    resp = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body:
        formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
    })
  } catch {
    // Fixed message: never echo the request (it may carry a Gemini key).
    throw new ApiError('Network error', 0, 'unknown')
  }

  if (resp.status === 204) return undefined as T

  const payload = parsePayload(await resp.text())

  if (!resp.ok) {
    const error = toApiError(resp.status, payload)
    // Any 401 means the App_Session is missing, invalid, expired, or revoked.
    if (resp.status === 401) signOutLocally()
    throw error
  }

  return payload as T
}

// --- Multipart upload with progress ---------------------------------------------

export interface UploadOptions {
  /** Upload progress as a fraction in [0, 1] (`XMLHttpRequest.upload.onprogress`). */
  onProgress?: (fraction: number) => void
  /** Aborting rejects with a DOMException named `AbortError`. */
  signal?: AbortSignal
}

function abortError(): DOMException {
  return new DOMException('The upload was aborted', 'AbortError')
}

/**
 * POST a multipart body with upload progress. `fetch` has no upload progress
 * events, so this uses XMLHttpRequest with the same bearer session, 401
 * sign-out, and error classification as `request<T>()`.
 */
export function uploadForm<T>(
  path: string,
  formData: FormData,
  { onProgress, signal }: UploadOptions = {},
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError())
      return
    }

    const xhr = new XMLHttpRequest()
    const onAbortSignal = () => xhr.abort()
    const cleanup = () => signal?.removeEventListener('abort', onAbortSignal)

    xhr.open('POST', path)
    const token = getSessionToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    // No Content-Type: the browser supplies the multipart boundary.

    if (onProgress) {
      xhr.upload.onprogress = (event: ProgressEvent) => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.min(1, Math.max(0, event.loaded / event.total)))
        }
      }
      // The request body is fully sent; the server is now reading the deck.
      xhr.upload.onload = () => onProgress(1)
    }

    xhr.onload = () => {
      cleanup()
      const status = xhr.status
      if (status === 204) {
        resolve(undefined as T)
        return
      }
      const payload = parsePayload(xhr.responseText)
      if (status >= 200 && status < 300) {
        resolve(payload as T)
        return
      }
      const error = toApiError(status, payload)
      if (status === 401) signOutLocally()
      reject(error)
    }
    xhr.onerror = () => {
      cleanup()
      // Fixed message: never echo the request.
      reject(new ApiError('Network error', 0, 'unknown'))
    }
    xhr.onabort = () => {
      cleanup()
      reject(abortError())
    }

    signal?.addEventListener('abort', onAbortSignal, { once: true })
    xhr.send(formData)
  })
}

// Core typed HTTP client: auth token storage, request helper, and error mapping.
import { ApiError, type ApiErrorKind, type FieldError } from './types'

const TOKEN_KEY = 'jvt.token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token === null) localStorage.removeItem(TOKEN_KEY)
    else localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* ignore storage errors (e.g. private mode) */
  }
}

/** Map an HTTP response + parsed detail into a discriminated ApiError kind. */
function classify(status: number, detail: unknown): {
  kind: ApiErrorKind
  message: string
  fieldErrors: FieldError[]
} {
  // Generation errors carry a {code, message} detail object (Req 4.5, 4.6).
  if (detail && typeof detail === 'object' && 'code' in (detail as object)) {
    const code = String((detail as { code: unknown }).code)
    const message = String(
      (detail as { message?: unknown }).message ?? 'Request failed',
    )
    if (code === 'generation_timeout')
      return { kind: 'generation_timeout', message, fieldErrors: [] }
    if (code === 'generation_failed')
      return { kind: 'generation_failed', message, fieldErrors: [] }
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
    return { kind: 'validation', message, fieldErrors: errors }
  }

  const text =
    detail && typeof detail === 'object' && 'detail' in (detail as object)
      ? String((detail as { detail: unknown }).detail)
      : typeof detail === 'string'
        ? detail
        : null

  const message = text ?? `Request failed (${status})`

  switch (status) {
    case 401:
      return { kind: 'unauthorized', message, fieldErrors: [] }
    case 409: {
      const kind: ApiErrorKind = /revealed/i.test(message)
        ? 'not_revealed'
        : /already exists|duplicate/i.test(message)
          ? 'duplicate'
          : 'conflict'
      return { kind, message, fieldErrors: [] }
    }
    case 404:
      return { kind: 'not_found', message, fieldErrors: [] }
    case 500:
      return {
        kind: /scheduling/i.test(message) ? 'scheduling_not_saved' : 'unknown',
        message,
        fieldErrors: [],
      }
    default:
      return { kind: 'unknown', message, fieldErrors: [] }
  }
}

export interface RequestOptions {
  method?: string
  body?: unknown
  auth?: boolean
}

export async function request<T>(
  path: string,
  { method = 'GET', body, auth = true }: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
  }

  let resp: Response
  try {
    resp = await fetch(path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch (err) {
    throw new ApiError(
      err instanceof Error ? err.message : 'Network error',
      0,
      'unknown',
    )
  }

  if (resp.status === 204) return undefined as T

  let payload: unknown = null
  const raw = await resp.text()
  if (raw) {
    try {
      payload = JSON.parse(raw)
    } catch {
      payload = raw
    }
  }

  if (!resp.ok) {
    // FastAPI wraps error bodies as {detail: ...}; pass the detail to classify.
    const detail =
      payload && typeof payload === 'object' && 'detail' in (payload as object)
        ? (payload as { detail: unknown }).detail
        : payload
    const { kind, message, fieldErrors } = classify(resp.status, detail)
    throw new ApiError(message, resp.status, kind, fieldErrors)
  }

  return payload as T
}

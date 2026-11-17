import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getSessionToken,
  onSignedOut,
  request,
  setSessionToken,
} from './client'
import { ApiError } from './types'

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
  } as Response)
}

/** Run a request expected to fail and return the thrown ApiError. */
async function expectApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    return error as ApiError
  }
  throw new Error('expected the request to reject')
}

describe('request', () => {
  let stored: Map<string, string>

  beforeEach(() => {
    stored = new Map()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
      removeItem: (key: string) => stored.delete(key),
    })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('attaches only the stored first-party bearer session to authenticated requests', async () => {
    setSessionToken('app-session')
    const fetchMock = mockFetch(200, { ok: true })
    vi.stubGlobal('fetch', fetchMock)

    await request('/api/words')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/words',
      expect.objectContaining({
        credentials: 'same-origin',
        headers: { Authorization: 'Bearer app-session' },
      }),
    )
  })

  it('maps per-field 400 validation errors', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(400, {
        detail: {
          message: 'Invalid word input',
          errors: [{ field: 'word_form', message: 'must not be empty' }],
        },
      }),
    )
    const error = await expectApiError(
      request('/api/words', { method: 'POST', body: {} }),
    )
    expect(error.kind).toBe('validation')
    expect(error.fieldErrors).toHaveLength(1)
    expect(error.fieldErrors[0].field).toBe('word_form')
  })

  it('maps duplicate 409', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(409, { detail: 'Word form already exists: 食べる' }),
    )
    const error = await expectApiError(
      request('/api/words', { method: 'POST', body: {} }),
    )
    expect(error.kind).toBe('duplicate')
  })

  it.each([
    [409, 'gemini_key_required'],
    [422, 'invalid_gemini_key'],
    [429, 'gemini_quota_exceeded'],
    [502, 'generation_timeout'],
    [502, 'generation_failed'],
  ] as const)('maps %s sentence error code %s', async (status, code) => {
    vi.stubGlobal(
      'fetch',
      mockFetch(status, { detail: { code, message: `mapped ${code}` } }),
    )

    const error = await expectApiError(request('/api/review/sentence'))

    expect(error.code).toBe(code)
    expect(error.status).toBe(status)
    expect(error.message).toBe(`mapped ${code}`)
  })

  it.each([
    [400, 'invalid_key_format'],
    [400, 'invalid_key'],
    [409, 'key_verification_in_progress'],
    [503, 'could_not_verify'],
    [503, 'key_storage_unavailable'],
    [500, 'key_storage_failed'],
  ] as const)('maps %s Gemini key error code %s', async (status, code) => {
    vi.stubGlobal(
      'fetch',
      mockFetch(status, { detail: { code, message: `mapped ${code}` } }),
    )

    const error = await expectApiError(
      request('/api/gemini-key', { method: 'PUT', body: { api_key: 'x' } }),
    )

    expect(error.code).toBe(code)
    expect(error.status).toBe(status)
  })

  it('maps FastAPI list-shaped 422 to invalid_rating but object 422 by its code', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(422, { detail: [{ loc: ['body', 'rating'], msg: 'bad' }] }),
    )
    expect((await expectApiError(request('/api/x'))).code).toBe('invalid_rating')

    vi.stubGlobal(
      'fetch',
      mockFetch(422, { detail: { code: 'invalid_gemini_key', message: 'm' } }),
    )
    expect((await expectApiError(request('/api/x'))).code).toBe(
      'invalid_gemini_key',
    )
  })

  it('treats any 401 as signed out: clears the session and notifies listeners', async () => {
    setSessionToken('app-session')
    const listener = vi.fn()
    const unsubscribe = onSignedOut(listener)
    vi.stubGlobal('fetch', mockFetch(401, { detail: 'Not authenticated' }))

    const error = await expectApiError(request('/api/auth/me'))
    unsubscribe()

    expect(error.code).toBe('unauthorized')
    expect(getSessionToken()).toBeNull()
    expect(listener).toHaveBeenCalledOnce()
  })

  it('does not echo a submitted key when the network fails', async () => {
    const key = 'AIza' + 'a'.repeat(35)
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError(key)))

    const error = await expectApiError(
      request('/api/gemini-key', { method: 'PUT', body: { api_key: key } }),
    )

    expect(error.status).toBe(0)
    expect(error.message).not.toContain(key)
  })

  it.each([
    [400, 'login_not_verified'],
    [400, 'sign_in_not_completed'],
    [503, 'sign_in_service_unreachable'],
    [401, 'account_not_verified'],
    [503, 'sign_in_unavailable'],
  ] as const)('maps top-level OAuth callback error %s', async (status, code) => {
    vi.stubGlobal(
      'fetch',
      mockFetch(status, {
        code,
        message: `mapped ${code}`,
        retry_url: '/api/auth/login',
      }),
    )

    const error = await expectApiError(
      request('/api/auth/callback', { auth: false }),
    )

    expect(error.kind).toBe(code)
    expect(error.message).toBe(`mapped ${code}`)
  })

  it('maps not-revealed, invalid-rating, and scheduling failures', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(409, { detail: 'The word must be revealed before rating' }),
    )
    expect((await expectApiError(request('/api/x'))).kind).toBe('not_revealed')

    vi.stubGlobal('fetch', mockFetch(422, { detail: [] }))
    expect((await expectApiError(request('/api/x'))).kind).toBe('invalid_rating')

    vi.stubGlobal(
      'fetch',
      mockFetch(500, { detail: 'Scheduling not saved; please try again' }),
    )
    expect((await expectApiError(request('/api/x'))).kind).toBe(
      'scheduling_not_saved',
    )
  })
})

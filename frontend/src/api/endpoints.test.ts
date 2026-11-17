import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getSessionToken, setSessionToken } from './client'
import {
  AUTH_LOGIN_PATH,
  deleteGeminiKey,
  getGeminiKeyStatus,
  getMe,
  handleAuthCallback,
  loginUrl,
  logout,
  putGeminiKey,
  startLogin,
} from './endpoints'

function successfulResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
  } as Response
}

describe('auth endpoints', () => {
  let stored: Map<string, string>

  beforeEach(() => {
    stored = new Map()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
      removeItem: (key: string) => stored.delete(key),
    })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('starts login by navigating to the backend GET redirect endpoint', () => {
    const navigate = vi.fn()

    startLogin(navigate)

    expect(navigate).toHaveBeenCalledOnce()
    expect(navigate).toHaveBeenCalledWith(AUTH_LOGIN_PATH)
  })

  it('handles the OAuth callback without bearer auth and stores the app session', async () => {
    setSessionToken('stale-session')
    const fetchMock = vi.fn().mockResolvedValue(
      successfulResponse({
        access_token: 'first-party-app-session',
        token_type: 'bearer',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await handleAuthCallback(
      new URLSearchParams({ code: 'provider code', state: 'state-value' }),
    )

    expect(result.access_token).toBe('first-party-app-session')
    expect(getSessionToken()).toBe('first-party-app-session')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/callback?code=provider+code&state=state-value',
      expect.objectContaining({
        method: 'GET',
        headers: {},
        credentials: 'same-origin',
      }),
    )
  })

  it('clears the first-party session on logout', async () => {
    setSessionToken('first-party-app-session')
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(successfulResponse({ success: true })),
    )

    const result = await logout()

    expect(result).toEqual({ success: true })
    expect(getSessionToken()).toBeNull()
  })
})

describe('me and Gemini key endpoints', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', {
      getItem: () => 'app-session',
      setItem: () => undefined,
      removeItem: () => undefined,
    })
  })
  afterEach(() => vi.unstubAllGlobals())

  function stubFetch(body: unknown) {
    const fetchMock = vi.fn().mockResolvedValue(successfulResponse(body))
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  function call(fetchMock: ReturnType<typeof vi.fn>, index = 0) {
    return fetchMock.mock.calls[index] as [string, RequestInit]
  }

  it('returns /api/auth/login as the full-page login URL', () => {
    expect(loginUrl()).toBe('/api/auth/login')
  })

  it('gets the signed-in Learner with the key status', async () => {
    const me = {
      email: 'a@example.com',
      name: null,
      gemini_key: { connected: true, key_hint: 'abcd' },
    }
    const fetchMock = stubFetch(me)

    expect(await getMe()).toEqual(me)
    const [path, options] = call(fetchMock)
    expect(path).toBe('/api/auth/me')
    expect(options.method).toBe('GET')
    expect(options.headers).toEqual({ Authorization: 'Bearer app-session' })
  })

  it('gets, puts, and deletes the Gemini key on /api/gemini-key', async () => {
    const key = 'AIza' + 'b'.repeat(35)
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        successfulResponse({ connected: false, key_hint: null }),
      )
      .mockResolvedValueOnce(
        successfulResponse({
          connected: true,
          key_hint: 'bbbb',
          quota_exhausted_notice: false,
        }),
      )
      .mockResolvedValueOnce(
        successfulResponse({ connected: false, key_hint: null }),
      )
    vi.stubGlobal('fetch', fetchMock)

    expect(await getGeminiKeyStatus()).toEqual({
      connected: false,
      key_hint: null,
    })
    expect((await putGeminiKey(key)).key_hint).toBe('bbbb')
    expect(await deleteGeminiKey()).toEqual({ connected: false, key_hint: null })

    const [getPath, getOptions] = call(fetchMock, 0)
    expect([getPath, getOptions.method]).toEqual(['/api/gemini-key', 'GET'])

    const [putPath, putOptions] = call(fetchMock, 1)
    expect([putPath, putOptions.method]).toEqual(['/api/gemini-key', 'PUT'])
    expect(JSON.parse(putOptions.body as string)).toEqual({ api_key: key })
    expect(putPath).not.toContain(key)

    const [deletePath, deleteOptions] = call(fetchMock, 2)
    expect([deletePath, deleteOptions.method]).toEqual([
      '/api/gemini-key',
      'DELETE',
    ])
  })
})

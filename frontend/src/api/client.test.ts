import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { request } from './client'
import { ApiError } from './types'

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
  } as Response)
}

/** Run a request expected to fail and return the thrown ApiError. */
async function expectApiError(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError)
    return e as ApiError
  }
  throw new Error('expected the request to reject')
}

describe('request error mapping', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    })
  })
  afterEach(() => vi.unstubAllGlobals())

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
    const err = await expectApiError(
      request('/api/words', { method: 'POST', body: {} }),
    )
    expect(err.kind).toBe('validation')
    expect(err.fieldErrors).toHaveLength(1)
    expect(err.fieldErrors[0].field).toBe('word_form')
  })

  it('maps duplicate 409', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(409, { detail: 'Word form already exists: 食べる' }),
    )
    const err = await expectApiError(
      request('/api/words', { method: 'POST', body: {} }),
    )
    expect(err.kind).toBe('duplicate')
  })

  it('maps generation timeout and failure codes', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(502, {
        detail: { code: 'generation_timeout', message: 'timed out' },
      }),
    )
    expect((await expectApiError(request('/api/x'))).kind).toBe(
      'generation_timeout',
    )

    vi.stubGlobal(
      'fetch',
      mockFetch(502, {
        detail: { code: 'generation_failed', message: 'boom' },
      }),
    )
    expect((await expectApiError(request('/api/x'))).kind).toBe(
      'generation_failed',
    )
  })

  it('maps not-revealed 409 and scheduling 500', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(409, { detail: 'The word must be revealed before rating' }),
    )
    expect((await expectApiError(request('/api/x'))).kind).toBe('not_revealed')

    vi.stubGlobal(
      'fetch',
      mockFetch(500, { detail: 'Scheduling not saved; please try again' }),
    )
    expect((await expectApiError(request('/api/x'))).kind).toBe(
      'scheduling_not_saved',
    )
  })
})

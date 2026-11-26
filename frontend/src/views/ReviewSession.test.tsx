import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import ReviewSession from './ReviewSession'

const VALID_KEY = 'AIza' + 'b'.repeat(31) + 'HINT'

const WORD = {
  id: 'w1',
  word_form: '懐かしい',
  reading: 'なつかしい',
  meaning: '그리운',
  next_review_date: '2024-01-01',
  created_at: '2024-01-01T00:00:00Z',
}

const SENTENCE = {
  text: '懐かしい歌',
  tokens: [
    { surface: '懐かしい', reading: 'なつかしい', meaning: '그리운', is_target: true, contains_kanji: true },
    { surface: '歌', reading: 'うた', meaning: '노래', is_target: false, contains_kanji: true },
  ],
}

function respond(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as Response
}

const SESSION_PATH = '/api/review/sessions'
const SENTENCE_PATH = '/api/review/sessions/s1/words/w1/sentence'

/** Route stubbed backend responses by "METHOD path"; each entry is a queue. */
function stubBackend(routes: Record<string, Response[]>) {
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const queue = routes[`${init?.method ?? 'GET'} ${path}`]
    if (!queue || queue.length === 0) throw new Error(`unexpected ${init?.method} ${path}`)
    return queue.length > 1 ? queue.shift()! : queue[0]
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('ReviewSession Gemini key states', () => {
  beforeEach(() => {
    const stored = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
      removeItem: (key: string) => stored.delete(key),
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('presents the key guide instead of a session when no key is stored', async () => {
    stubBackend({
      [`POST ${SESSION_PATH}`]: [
        respond(409, { detail: { code: 'gemini_key_required', message: 'x' } }),
      ],
    })
    const onKeyStatusChange = vi.fn()
    render(<ReviewSession onKeyStatusChange={onKeyStatusChange} />)

    const panel = await screen.findByTestId('session-key-required')
    expect(within(panel).getByTestId('gemini-key-setup')).toBeInTheDocument()
    expect(within(panel).getByTestId('ai-studio-link')).toBeInTheDocument()
    expect(onKeyStatusChange).toHaveBeenCalledWith({ connected: false, key_hint: null })
  })

  it('opens the guide on a sentence-route key error and re-requests the same word once connected', async () => {
    const fetchMock = stubBackend({
      [`POST ${SESSION_PATH}`]: [respond(200, { session_id: 's1', due_words: [WORD] })],
      [`POST ${SENTENCE_PATH}`]: [
        respond(409, { detail: { code: 'gemini_key_required', message: 'x' } }),
        respond(200, SENTENCE),
      ],
      'PUT /api/gemini-key': [
        respond(200, { connected: true, key_hint: 'HINT', quota_exhausted_notice: false }),
      ],
    })
    render(<ReviewSession />)

    expect(await screen.findByTestId('generation-error')).toHaveTextContent('Gemini API 키를 설정하세요')
    fireEvent.click(screen.getByTestId('open-key-setup'))

    const dialog = screen.getByTestId('key-dialog')
    fireEvent.change(within(dialog).getByLabelText('Gemini API 키'), { target: { value: VALID_KEY } })
    fireEvent.click(within(dialog).getByRole('button', { name: '연결 확인' }))

    expect(await screen.findByTestId('sentence')).toHaveTextContent('懐かしい歌')
    expect(screen.queryByTestId('key-dialog')).not.toBeInTheDocument()
    const sentenceCalls = fetchMock.mock.calls.filter(([path]) => path === SENTENCE_PATH)
    expect(sentenceCalls).toHaveLength(2)
  })

  it('offers key replacement on invalid_gemini_key', async () => {
    stubBackend({
      [`POST ${SESSION_PATH}`]: [respond(200, { session_id: 's1', due_words: [WORD] })],
      [`POST ${SENTENCE_PATH}`]: [
        respond(422, { detail: { code: 'invalid_gemini_key', message: 'x' } }),
      ],
      'GET /api/gemini-key': [respond(200, { connected: true, key_hint: 'abcd' })],
    })
    render(<ReviewSession />)

    expect(await screen.findByTestId('generation-error')).toHaveTextContent('키를 쓸 수 없어요')
    fireEvent.click(screen.getByTestId('replace-key'))
    const dialog = screen.getByTestId('key-dialog')
    expect(within(dialog).getByText('새 Gemini API 키로 바꾸기')).toBeInTheDocument()
    expect(within(dialog).getByTestId('key-form')).toBeInTheDocument()
    expect(await within(dialog).findByTestId('key-hint')).toHaveTextContent('····abcd')
  })

  it('shows a wait-and-retry message on gemini_quota_exceeded and retries the same word', async () => {
    const fetchMock = stubBackend({
      [`POST ${SESSION_PATH}`]: [respond(200, { session_id: 's1', due_words: [WORD] })],
      [`POST ${SENTENCE_PATH}`]: [
        respond(429, { detail: { code: 'gemini_quota_exceeded', message: 'x' } }),
        respond(200, SENTENCE),
      ],
    })
    render(<ReviewSession />)

    const error = await screen.findByTestId('generation-error')
    expect(error).toHaveTextContent('사용량 한도에 도달했어요')
    expect(error).toHaveTextContent('기다렸다가 다시 시도')
    fireEvent.click(screen.getByTestId('retry-generation'))

    expect(await screen.findByTestId('sentence')).toHaveTextContent('懐かしい歌')
    expect(fetchMock.mock.calls.filter(([path]) => path === SENTENCE_PATH)).toHaveLength(2)
  })

  it('reveals with Space and rates with a digit key', async () => {
    const fetchMock = stubBackend({
      [`POST ${SESSION_PATH}`]: [respond(200, { session_id: 's1', due_words: [WORD] })],
      [`POST ${SENTENCE_PATH}`]: [respond(200, SENTENCE)],
      'POST /api/review/sessions/s1/words/w1/reveal': [respond(200, { id: 'w1', word_form: '懐かしい', reading: 'なつかしい', meaning: '그리운' })],
      'POST /api/review/sessions/s1/words/w1/rating': [
        respond(200, {
          word_id: 'w1',
          next_review_date: '2024-01-05',
          session_complete: true,
          next_word: null,
          summary: { total_reviewed: 1, counts_by_rating: { '3': 1 } },
        }),
      ],
    })
    render(<ReviewSession />)
    await screen.findByTestId('sentence')

    fireEvent.keyDown(document, { key: ' ' })
    expect(await screen.findByTestId('answer')).toHaveTextContent('그리운')

    fireEvent.keyDown(document, { key: '3' })

    expect(await screen.findByTestId('summary')).toBeInTheDocument()
    expect(screen.getByTestId('summary-count-3')).toHaveTextContent('1')
    const rating = fetchMock.mock.calls.find(([path]) => path.endsWith('/rating'))
    expect(rating?.[1]?.body).toBe(JSON.stringify({ rating: 3 }))
  })

  it('stops a stale session with an explanation instead of retrying silently', async () => {
    stubBackend({
      [`POST ${SESSION_PATH}`]: [respond(200, { session_id: 's1', due_words: [WORD] })],
      [`POST ${SENTENCE_PATH}`]: [respond(200, SENTENCE)],
      'POST /api/review/sessions/s1/words/w1/reveal': [respond(200, { id: 'w1', word_form: '懐かしい', reading: 'なつかしい', meaning: '그리운' })],
      'POST /api/review/sessions/s1/words/w1/rating': [
        respond(410, {
          detail: { code: 'session_expired', message: 'This review session has expired.' },
        }),
      ],
    })
    render(<ReviewSession />)
    await screen.findByTestId('sentence')

    fireEvent.click(screen.getByTestId('reveal-button'))
    await screen.findByTestId('answer')
    fireEvent.click(screen.getByTestId('rating-3'))

    expect(await screen.findByTestId('session-error')).toHaveTextContent('복습 세션이 만료되었어요')
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument()
  })
})

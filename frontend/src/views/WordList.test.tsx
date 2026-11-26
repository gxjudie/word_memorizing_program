import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import WordList from './WordList'

const NEKO = {
  id: 'w1',
  word_form: '猫',
  reading: 'ねこ',
  meaning: '고양이',
  next_review_date: '2024-01-01',
  created_at: '2024-01-01T00:00:00Z',
}

const INU = {
  id: 'w2',
  word_form: '犬',
  reading: 'いぬ',
  meaning: '개',
  next_review_date: '2099-01-01',
  created_at: '2024-01-02T00:00:00Z',
}

function respond(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as Response
}

function emptyResponse(status: number): Response {
  return { ok: status >= 200 && status < 300, status, text: async () => '' } as Response
}

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

function bodyOf(call: [string, RequestInit | undefined]): unknown {
  return JSON.parse(String(call[1]?.body))
}

describe('WordList', () => {
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

  it('lists the collection with a count and a review badge', async () => {
    stubBackend({ 'GET /api/words': [respond(200, [NEKO, INU])] })
    render(<WordList />)

    expect(await screen.findByTestId('word-row-猫')).toBeInTheDocument()
    expect(screen.getByTestId('word-count')).toHaveTextContent('단어 2개')
    // Newest first: the dog was created later.
    const rows = screen.getAllByRole('listitem')
    expect(within(rows[0]).getByText('犬')).toBeInTheDocument()
    expect(within(screen.getByTestId('word-row-犬')).getByText('1월 1일 복습')).toBeInTheDocument()
  })

  it('shows a first-word empty state', async () => {
    stubBackend({ 'GET /api/words': [respond(200, [])] })
    render(<WordList onAdd={vi.fn()} />)

    const empty = await screen.findByTestId('word-list-empty')
    expect(within(empty).getByRole('button', { name: '단어 추가하기' })).toBeInTheDocument()
  })

  it('filters by word, reading, or meaning', async () => {
    stubBackend({ 'GET /api/words': [respond(200, [NEKO, INU])] })
    render(<WordList />)
    await screen.findByTestId('word-row-猫')

    const search = screen.getByRole('searchbox', { name: '단어 검색' })

    fireEvent.change(search, { target: { value: 'ねこ' } })
    expect(screen.getByTestId('word-row-猫')).toBeInTheDocument()
    expect(screen.queryByTestId('word-row-犬')).not.toBeInTheDocument()

    fireEvent.change(search, { target: { value: '개' } })
    expect(screen.getByTestId('word-row-犬')).toBeInTheDocument()

    fireEvent.change(search, { target: { value: '없는말' } })
    expect(screen.getByTestId('word-list-no-results')).toHaveTextContent('없는말')
  })

  it('edits a word without touching its review date', async () => {
    const updated = { ...NEKO, meaning: '고양이 (수정)' }
    const fetchMock = stubBackend({
      'GET /api/words': [respond(200, [NEKO])],
      'PATCH /api/words/w1': [respond(200, updated)],
    })
    render(<WordList />)
    await screen.findByTestId('word-row-猫')

    fireEvent.click(screen.getByRole('button', { name: '猫 수정' }))
    const dialog = screen.getByTestId('word-editor')
    const meaning = within(dialog).getByLabelText('뜻')
    fireEvent.change(meaning, { target: { value: '고양이 (수정)' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '저장' }))

    await screen.findByTestId('word-list-notice')
    expect(screen.queryByTestId('word-editor')).not.toBeInTheDocument()
    expect(screen.getByText('고양이 (수정)')).toBeInTheDocument()

    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')
    expect(patch).toBeDefined()
    expect(bodyOf(patch as [string, RequestInit])).toEqual({
      word_form: '猫',
      reading: 'ねこ',
      meaning: '고양이 (수정)',
    })
    // The response's next_review_date is still the original one.
    expect(NEKO.next_review_date).toBe('2024-01-01')
  })

  it('reports a duplicate word form without closing the editor', async () => {
    stubBackend({
      'GET /api/words': [respond(200, [NEKO, INU])],
      'PATCH /api/words/w2': [
        respond(409, { detail: 'A word with this form already exists in your collection' }),
      ],
    })
    render(<WordList />)
    await screen.findByTestId('word-row-犬')

    fireEvent.click(screen.getByRole('button', { name: '犬 수정' }))
    const dialog = screen.getByTestId('word-editor')
    fireEvent.change(within(dialog).getByLabelText('일본어 단어'), { target: { value: '猫' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '저장' }))

    expect(await within(dialog).findByTestId('word-editor-error')).toHaveTextContent(
      '같은 단어가 이미 단어장에 있어요',
    )
  })

  it('deletes a word after confirmation', async () => {
    const fetchMock = stubBackend({
      'GET /api/words': [respond(200, [NEKO, INU])],
      'DELETE /api/words/w1': [emptyResponse(204)],
    })
    render(<WordList />)
    await screen.findByTestId('word-row-猫')

    fireEvent.click(screen.getByRole('button', { name: '猫 삭제' }))
    const dialog = screen.getByTestId('word-delete-dialog')
    fireEvent.click(within(dialog).getByTestId('confirm-delete'))

    await waitFor(() => expect(screen.queryByTestId('word-row-猫')).not.toBeInTheDocument())
    expect(screen.getByTestId('word-row-犬')).toBeInTheDocument()
    expect(screen.getByTestId('word-list-notice')).toHaveTextContent('삭제했어요')
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')).toHaveLength(1)
  })

  it('closes the delete dialog on Escape', async () => {
    stubBackend({ 'GET /api/words': [respond(200, [NEKO])] })
    render(<WordList />)
    await screen.findByTestId('word-row-猫')

    fireEvent.click(screen.getByRole('button', { name: '猫 삭제' }))
    expect(screen.getByTestId('word-delete-dialog')).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('word-delete-dialog')).not.toBeInTheDocument()
  })
})

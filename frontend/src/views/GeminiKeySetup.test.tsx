import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import GeminiKeySetup, { AI_STUDIO_API_KEY_URL } from './GeminiKeySetup'

const VALID_KEY = 'AIza' + 'a'.repeat(31) + 'WXYZ'

function respond(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as Response
}

const NOT_CONNECTED = { connected: false, key_hint: null }

describe('GeminiKeySetup', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    const stored = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
      removeItem: (key: string) => stored.delete(key),
    })
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows the five steps in order with a safe AI Studio link and all notices', () => {
    render(<GeminiKeySetup status={NOT_CONNECTED} />)

    const steps = within(screen.getByTestId('key-steps')).getAllByRole('listitem')
    expect(steps).toHaveLength(5)
    expect(steps.map((step) => step.querySelector('p')?.textContent)).toEqual([
      '1단계: Google AI Studio 열기',
      '2단계: Google 계정으로 로그인',
      '3단계: API 키 만들기',
      '4단계: 키 복사하기',
      '5단계: 붙여넣고 연결 확인',
    ])

    const link = screen.getByTestId('ai-studio-link')
    expect(link).toHaveAttribute('href', AI_STUDIO_API_KEY_URL)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')

    const notices = screen.getByTestId('key-notices').textContent ?? ''
    expect(notices).toContain('무료')
    expect(notices).toContain('결제 카드가 필요 없어요')
    expect(notices).toContain('사용량 제한')
    expect(notices).toContain('만 18세 이상')
    expect(notices).toMatch(/EEA.*스위스.*영국.*유료 키/)
  })

  it('uses a password input with a show/hide toggle', () => {
    render(<GeminiKeySetup status={NOT_CONNECTED} />)
    const input = screen.getByLabelText('Gemini API 키')
    expect(input).toHaveAttribute('type', 'password')
    expect(input).toHaveAttribute('autocomplete', 'off')
    expect(input).toHaveAttribute('spellcheck', 'false')

    fireEvent.click(screen.getByRole('button', { name: '키 보기' }))
    expect(input).toHaveAttribute('type', 'text')
    fireEvent.click(screen.getByRole('button', { name: '키 숨기기' }))
    expect(input).toHaveAttribute('type', 'password')
  })

  it('accepts a new-style AQ. key without showing the format hint or blocking submit', () => {
    render(<GeminiKeySetup status={NOT_CONNECTED} />)
    const input = screen.getByLabelText('Gemini API 키')
    const aqKey = 'AQ.' + 'c'.repeat(40)

    fireEvent.change(input, { target: { value: aqKey } })

    // No soft format hint for a well-formed AQ. key...
    expect(screen.getByTestId('format-hint')).not.toHaveTextContent('지금')
    // ...and the connect button is enabled.
    expect(screen.getByRole('button', { name: '연결 확인' })).toBeEnabled()
  })

  it('hints at a bad format but still lets the backend decide', async () => {
    fetchMock.mockResolvedValue(
      respond(400, { detail: { code: 'invalid_key_format', message: 'bad format' } }),
    )
    render(<GeminiKeySetup status={NOT_CONNECTED} />)
    fireEvent.change(screen.getByLabelText('Gemini API 키'), { target: { value: 'abc' } })
    expect(screen.getByTestId('format-hint')).toHaveTextContent('지금 3자')

    fireEvent.click(screen.getByRole('button', { name: '연결 확인' }))
    expect(await screen.findByTestId('key-feedback')).toHaveTextContent('키 형식이 맞지 않아요')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('disables the form while verifying, then shows the hint and clears the key', async () => {
    let resolve!: (value: Response) => void
    fetchMock.mockReturnValue(new Promise<Response>((r) => (resolve = r)))
    const onConnected = vi.fn()
    render(<GeminiKeySetup status={NOT_CONNECTED} onConnected={onConnected} />)

    const input = screen.getByLabelText('Gemini API 키')
    fireEvent.change(input, { target: { value: `  ${VALID_KEY}  ` } })
    fireEvent.click(screen.getByRole('button', { name: '연결 확인' }))

    const busy = await screen.findByRole('button', { name: /확인하는 중/ })
    expect(busy).toBeDisabled()
    expect(input).toBeDisabled()
    expect(screen.getByTestId('verify-spinner')).toBeInTheDocument()

    const [path, init] = fetchMock.mock.calls[0]
    expect(path).toBe('/api/gemini-key')
    expect(init.method).toBe('PUT')

    resolve(respond(200, { connected: true, key_hint: 'WXYZ', quota_exhausted_notice: false }))

    expect(await screen.findByTestId('key-hint')).toHaveTextContent('····WXYZ')
    expect(screen.queryByTestId('key-form')).not.toBeInTheDocument()
    expect(screen.getByTestId('key-feedback')).toHaveTextContent('연결 완료')
    expect(onConnected).toHaveBeenCalledTimes(1)
    expect(document.body.textContent).not.toContain(VALID_KEY)
  })

  it('shows the quota-exhausted notice when the key is stored on a quota rejection', async () => {
    fetchMock.mockResolvedValue(
      respond(200, { connected: true, key_hint: 'WXYZ', quota_exhausted_notice: true }),
    )
    render(<GeminiKeySetup status={NOT_CONNECTED} />)
    fireEvent.change(screen.getByLabelText('Gemini API 키'), { target: { value: VALID_KEY } })
    fireEvent.click(screen.getByRole('button', { name: '연결 확인' }))

    expect(await screen.findByTestId('key-feedback')).toHaveTextContent('사용량 한도에 도달했어요')
    expect(screen.getByTestId('key-hint')).toHaveTextContent('····WXYZ')
  })

  it.each([
    ['invalid_key', 400, '사용할 수 없는 키예요'],
    ['could_not_verify', 503, '지금은 키를 확인할 수 없어요'],
    ['key_verification_in_progress', 409, '이미 키를 확인하고 있어요'],
    ['key_storage_unavailable', 503, '키를 저장할 수 없는 상태예요'],
    ['key_storage_failed', 500, '키를 저장하지 못했어요'],
  ])('maps %s to a Korean message and keeps the form', async (code, status, title) => {
    fetchMock.mockResolvedValue(respond(status, { detail: { code, message: 'x' } }))
    render(<GeminiKeySetup status={NOT_CONNECTED} />)
    fireEvent.change(screen.getByLabelText('Gemini API 키'), { target: { value: VALID_KEY } })
    fireEvent.click(screen.getByRole('button', { name: '연결 확인' }))

    expect(await screen.findByTestId('key-feedback')).toHaveTextContent(title)
    expect(screen.getByTestId('key-form')).toBeInTheDocument()
  })

  it('keeps the current key visible while replacing and removes only after confirmation', async () => {
    fetchMock.mockResolvedValue(respond(200, NOT_CONNECTED))
    const onStatusChange = vi.fn()
    render(
      <GeminiKeySetup status={{ connected: true, key_hint: 'abcd' }} onStatusChange={onStatusChange} />,
    )
    expect(screen.getByTestId('key-hint')).toHaveTextContent('····abcd')

    fireEvent.click(screen.getByRole('button', { name: '다른 키로 바꾸기' }))
    expect(screen.getByLabelText('새 Gemini API 키')).toBeInTheDocument()
    expect(screen.getByTestId('key-hint')).toHaveTextContent('····abcd')
    fireEvent.click(screen.getByRole('button', { name: '취소' }))

    fireEvent.click(screen.getByRole('button', { name: '키 삭제' }))
    expect(fetchMock).not.toHaveBeenCalled()
    fireEvent.click(within(screen.getByTestId('remove-confirm')).getByRole('button', { name: '삭제' }))

    await waitFor(() => expect(onStatusChange).toHaveBeenCalledWith(NOT_CONNECTED))
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE')
    expect(screen.queryByTestId('key-connected')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Gemini API 키')).toBeInTheDocument()
  })

  it('offers a "later" control while not connected', () => {
    const onLater = vi.fn()
    render(<GeminiKeySetup status={NOT_CONNECTED} onLater={onLater} />)
    fireEvent.click(screen.getByRole('button', { name: /나중에/ }))
    expect(onLater).toHaveBeenCalled()
  })
})

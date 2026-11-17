import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import App from './App'
import { getSessionToken, setSessionToken } from './api/client'

function respond(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as Response
}

describe('App', () => {
  beforeEach(() => {
    // In-memory storage: newer Node versions shadow jsdom's localStorage
    // with an unusable global, same as in client.test.ts.
    const stored = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
      removeItem: (key: string) => stored.delete(key),
    })
  })

  afterEach(() => {
    setSessionToken(null)
    vi.unstubAllGlobals()
    window.history.replaceState({}, '', '/')
  })

  it('renders the app heading', () => {
    render(<App />)
    expect(
      screen.getByRole('heading', { name: /Japanese Vocabulary Trainer/i }),
    ).toBeInTheDocument()
  })

  it('shows the Google sign-in entry point when signed out', () => {
    render(<App />)
    expect(screen.getByRole('button', { name: 'Google로 계속하기' })).toBeInTheDocument()
  })

  it('returns to the Login view when any request answers 401', async () => {
    setSessionToken('expired-session')
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(respond(401, { detail: 'Not authenticated' })),
    )

    render(<App />)

    expect(
      await screen.findByRole('button', { name: 'Google로 계속하기' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Log out' })).not.toBeInTheDocument()
    expect(getSessionToken()).toBeNull()
  })

  it('opens the Gemini key guide after sign-in when no key is connected, and "later" goes to Add Word', async () => {
    setSessionToken('app-session')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        respond(200, {
          email: 'learner@example.com',
          name: 'Learner',
          gemini_key: { connected: false, key_hint: null },
        }),
      ),
    )

    render(<App />)

    expect(await screen.findByTestId('gemini-key-setup')).toBeInTheDocument()
    expect(screen.getByTestId('header-key-entry')).toHaveAccessibleName(/연결 안 됨/)
    expect(screen.getByTestId('header-learner')).toHaveTextContent('Learner')

    fireEvent.click(screen.getByRole('button', { name: /나중에/ }))
    expect(screen.getByRole('button', { name: 'Add word' })).toBeInTheDocument()
  })

  it('shows the key hint in the header and keeps review as the start view when connected', async () => {
    setSessionToken('app-session')
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) =>
        path === '/api/auth/me'
          ? respond(200, {
              email: 'learner@example.com',
              name: null,
              gemini_key: { connected: true, key_hint: 'abcd' },
            })
          : respond(200, { status: 'no_due_words', message: 'No words due' }),
      ),
    )

    render(<App />)

    expect(await screen.findByTestId('no-due')).toBeInTheDocument()
    expect(screen.getByTestId('header-key-entry')).toHaveAccessibleName(/연결됨 ····abcd/)
    fireEvent.click(screen.getByTestId('header-key-entry'))
    expect(screen.getByTestId('key-hint')).toHaveTextContent('····abcd')
  })

  it('returns to the Login view after logout', async () => {
    setSessionToken('app-session')
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) =>
        path === '/api/auth/logout'
          ? respond(200, { success: true })
          : respond(200, { status: 'no_due_words', message: 'No words due' }),
      ),
    )

    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }))

    expect(
      await screen.findByRole('button', { name: 'Google로 계속하기' }),
    ).toBeInTheDocument()
    expect(getSessionToken()).toBeNull()
  })
})

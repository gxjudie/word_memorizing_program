import { useCallback, useEffect, useState } from 'react'
import AddWord from './views/AddWord'
import GeminiKeySetup, { maskedHint } from './views/GeminiKeySetup'
import Login from './views/Login'
import ReviewSession from './views/ReviewSession'
import StudySettings from './views/StudySettings'
import { getSessionToken, onSignedOut } from './api/client'
import { getMe, logout } from './api/endpoints'
import type { GeminiKeyStatus, Me } from './api/types'

type Tab = 'add' | 'review'
type View = Tab | 'key' | 'settings'

const NAV_ITEMS: { id: Tab; label: string; eyebrow: string }[] = [
  { id: 'review', label: '오늘의 복습', eyebrow: '復習' },
  { id: 'add', label: '단어 추가', eyebrow: '追加' },
]

function readKeyStatus(me: Me | null | undefined): GeminiKeyStatus | null {
  const status = me?.gemini_key
  return status && typeof status.connected === 'boolean' ? status : null
}

export default function App() {
  const [authed, setAuthed] = useState<boolean>(() => getSessionToken() !== null)
  const [view, setView] = useState<View>('review')
  const [loginNotice, setLoginNotice] = useState(false)
  const [me, setMe] = useState<Me | null>(null)
  const [keyStatus, setKeyStatus] = useState<GeminiKeyStatus | null>(null)
  const [shellReady, setShellReady] = useState(false)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('auth') === 'success' && getSessionToken()) {
      setAuthed(true)
      setLoginNotice(true)
      window.history.replaceState({}, '', window.location.pathname)
      const timer = window.setTimeout(() => setLoginNotice(false), 4500)
      return () => window.clearTimeout(timer)
    }
  }, [])

  // Any 401 (missing, expired, or revoked App_Session) clears the stored
  // session in the API client; return to the "Google로 계속하기" entry point.
  useEffect(
    () =>
      onSignedOut(() => {
        setAuthed(false)
        setLoginNotice(false)
      }),
    [],
  )

  // After sign-in: load the Learner and the key status. With no key stored,
  // open the Key_Setup_Guide right away (adding words stays available).
  useEffect(() => {
    if (!authed) {
      setMe(null)
      setKeyStatus(null)
      setShellReady(false)
      return
    }
    let active = true
    getMe()
      .then((loaded) => {
        if (!active) return
        setMe(loaded)
        const status = readKeyStatus(loaded)
        setKeyStatus(status)
        if (status && !status.connected) setView('key')
      })
      .catch(() => {
        /* A 401 is handled by onSignedOut; otherwise continue without /me. */
      })
      .finally(() => {
        if (active) setShellReady(true)
      })
    return () => {
      active = false
    }
  }, [authed])

  const handleKeyStatusChange = useCallback((status: GeminiKeyStatus) => {
    setKeyStatus(status)
  }, [])

  if (!authed) return <Login />

  async function handleLogout() {
    try {
      await logout()
    } finally {
      setAuthed(false)
    }
  }

  const keyConnected = keyStatus?.connected === true
  const keyLabel = keyStatus === null ? '확인 중' : keyConnected ? maskedHint(keyStatus.key_hint) : '연결 안 됨'
  const learnerName = me?.name || me?.email || null

  return (
    <div className="min-h-screen bg-washi text-ink">
      <header className="sticky top-0 z-30 border-b border-line/80 bg-washi/90 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-2 px-4 py-3 sm:gap-4 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-vermilion font-serif-jp text-base font-bold text-white shadow-soft" aria-hidden="true">
              文
            </div>
            <div className="min-w-0">
              <h1 className="truncate font-serif-jp text-lg font-semibold tracking-tight sm:text-xl">
                <span className="sr-only">Japanese Vocabulary Trainer — </span>文脈で覚える
              </h1>
              <p className="hidden text-[11px] font-medium tracking-[0.16em] text-ink-faint sm:block">일본어 문맥 단어장</p>
            </div>
          </div>

          <nav className="flex items-center gap-1 rounded-2xl border border-line/80 bg-white/70 p-1" aria-label="주요 메뉴">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setView(item.id)}
                aria-current={view === item.id ? 'page' : undefined}
                className={
                  'rounded-xl px-3 py-2 text-sm font-semibold transition focus:outline-none focus:ring-2 focus:ring-forest/30 sm:px-4 ' +
                  (view === item.id
                    ? 'bg-forest text-white shadow-sm'
                    : 'text-ink-muted hover:bg-forest-soft hover:text-forest')
                }
              >
                <span className="mr-1.5 hidden font-serif-jp text-xs opacity-70 sm:inline">{item.eyebrow}</span>
                {item.label}
              </button>
            ))}
          </nav>

          <div className="flex shrink-0 items-center gap-1 sm:gap-2">
            <button
              type="button"
              onClick={() => setView('key')}
              aria-current={view === 'key' ? 'page' : undefined}
              aria-label={`Gemini 키 설정 (${keyConnected ? `연결됨 ${maskedHint(keyStatus?.key_hint ?? null)}` : keyStatus === null ? '확인 중' : '연결 안 됨'})`}
              title="Gemini 키 설정"
              data-testid="header-key-entry"
              className={
                'flex items-center gap-2 rounded-xl border px-2.5 py-2 text-sm font-semibold transition focus:outline-none focus:ring-4 focus:ring-forest/15 sm:px-3 ' +
                (view === 'key' ? 'border-forest/30 bg-forest-soft text-forest' : 'border-line/80 bg-white/70 text-ink-muted hover:text-ink')
              }
            >
              <span
                className={`h-2 w-2 rounded-full ${keyStatus === null ? 'bg-ink-faint/50' : keyConnected ? 'bg-forest' : 'bg-vermilion'}`}
                aria-hidden="true"
                data-testid="header-key-dot"
              />
              <span className="font-serif-jp" aria-hidden="true">鍵</span>
              <span className="hidden lg:inline">Gemini 키</span>
              <span className="hidden font-mono text-xs font-medium xl:inline" aria-hidden="true">{keyLabel}</span>
            </button>

            <button
              type="button"
              onClick={() => setView('settings')}
              aria-current={view === 'settings' ? 'page' : undefined}
              aria-label="학습 설정"
              title="학습 설정"
              data-testid="header-settings-entry"
              className={
                'flex items-center gap-2 rounded-xl border px-2.5 py-2 text-sm font-semibold transition focus:outline-none focus:ring-4 focus:ring-forest/15 sm:px-3 ' +
                (view === 'settings' ? 'border-forest/30 bg-forest-soft text-forest' : 'border-line/80 bg-white/70 text-ink-muted hover:text-ink')
              }
            >
              <span className="font-serif-jp" aria-hidden="true">設</span>
              <span className="hidden lg:inline">학습 설정</span>
            </button>

            {learnerName && (
              <span className="hidden max-w-[12rem] truncate text-sm text-ink-muted lg:inline" title={me?.email} data-testid="header-learner">
                {learnerName}
              </span>
            )}

            <button
              type="button"
              onClick={() => void handleLogout()}
              className="shrink-0 rounded-xl px-2.5 py-2 text-sm font-medium text-ink-muted transition hover:bg-white hover:text-ink focus:outline-none focus:ring-4 focus:ring-forest/15 sm:px-3"
              aria-label="Log out"
            >
              <span className="hidden sm:inline">로그아웃</span>
              <svg viewBox="0 0 24 24" className="h-5 w-5 sm:hidden" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                <path d="M10 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4M15 8l4 4-4 4m4-4H9" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>
        </div>
      </header>

      {loginNotice && (
        <div className="fixed right-4 top-20 z-40 flex max-w-sm items-center gap-3 rounded-2xl border border-forest/15 bg-white px-4 py-3 text-sm font-medium text-forest shadow-card" role="status">
          <span className="grid h-6 w-6 place-items-center rounded-full bg-forest text-xs text-white" aria-hidden="true">✓</span>
          Google 로그인이 완료되었습니다.
        </div>
      )}

      <main>
        {!shellReady ? (
          <div className="grid min-h-[60vh] place-items-center" data-testid="shell-loading">
            <div className="h-10 w-10 animate-spin rounded-full border-2 border-line border-t-vermilion" aria-label="불러오는 중" />
          </div>
        ) : view === 'key' ? (
          <GeminiKeySetup
            status={keyStatus}
            onStatusChange={handleKeyStatusChange}
            onLater={() => setView('add')}
            onContinue={() => setView('review')}
          />
        ) : view === 'settings' ? (
          <StudySettings />
        ) : view === 'review' ? (
          <ReviewSession onKeyStatusChange={handleKeyStatusChange} onLater={() => setView('add')} />
        ) : (
          <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 sm:py-12 lg:px-8">
            <AddWord onGoToReview={() => setView('review')} />
          </div>
        )}
      </main>
    </div>
  )
}

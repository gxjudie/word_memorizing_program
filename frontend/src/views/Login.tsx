import { useMemo, useState } from 'react'
import { startLogin } from '../api/endpoints'
import type { AuthErrorCode } from '../api/types'

// Callback bridge error codes (`/?auth_error=<code>`) mapped to Korean copy.
const AUTH_MESSAGES: Record<AuthErrorCode, { title: string; detail: string }> = {
  // Req 9.4: state missing, mismatched, expired, or already used.
  login_not_verified: {
    title: '로그인 요청을 확인할 수 없어요',
    detail: '보안을 위해 이 로그인 시도를 종료했습니다. 처음부터 다시 시도해 주세요.',
  },
  // Req 9.5: Google returned an error, or the Learner cancelled or denied.
  sign_in_not_completed: {
    title: '로그인이 완료되지 않았어요',
    detail: 'Google 로그인 화면에서 로그인을 취소했거나 계정 선택을 마치지 않았습니다.',
  },
  // Req 9.7: token exchange timed out, failed, or returned no ID token.
  sign_in_service_unreachable: {
    title: '로그인을 완료할 수 없어요',
    detail: 'Google과 통신하는 중 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.',
  },
  // Req 9.10: the Google ID token failed validation.
  account_not_verified: {
    title: '계정을 확인할 수 없어요',
    detail: 'Google 계정 정보를 확인하지 못했습니다. 이메일 인증이 완료된 계정으로 다시 로그인해 주세요.',
  },
  sign_in_unavailable: {
    title: '지금은 로그인할 수 없어요',
    detail: '로그인 설정 또는 서비스 상태를 확인한 뒤 다시 시도해 주세요.',
  },
}

function readAuthError(search: string): AuthErrorCode | null {
  const value = new URLSearchParams(search).get('auth_error')
  return value && value in AUTH_MESSAGES ? (value as AuthErrorCode) : null
}

export interface LoginProps {
  /** Full-page navigation used to start sign-in; injectable for tests. */
  navigate?: (url: string) => void
}

/**
 * Entry point for the backend-owned "Sign in with Google" flow
 * (Authorization Code + PKCE + OIDC). Also renders callback bridge failures,
 * each with a retry control that starts a brand-new login attempt.
 */
export function Login({ navigate }: LoginProps = {}) {
  const [starting, setStarting] = useState(false)
  const authError = useMemo(() => readAuthError(window.location.search), [])
  const error = authError ? AUTH_MESSAGES[authError] : null

  function handleLogin() {
    setStarting(true)
    // Navigates to loginUrl(); the backend issues a fresh state, nonce, and PKCE pair.
    startLogin(navigate)
  }

  return (
    <main className="relative min-h-screen overflow-hidden bg-washi px-5 py-8 text-ink sm:px-8 lg:px-12">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_12%_12%,rgba(224,119,85,0.16),transparent_32%),radial-gradient(circle_at_85%_80%,rgba(45,91,79,0.13),transparent_30%)]" />
      <div className="pointer-events-none absolute -right-24 top-16 h-80 w-80 rounded-full border border-vermilion/10" />
      <div className="pointer-events-none absolute -right-8 top-32 h-56 w-56 rounded-full border border-vermilion/10" />

      <div className="relative mx-auto flex min-h-[calc(100vh-4rem)] max-w-6xl flex-col">
        <header className="flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-2xl bg-vermilion text-lg font-bold text-white shadow-soft" aria-hidden="true">
            文
          </div>
          <div>
            <p className="font-serif-jp text-lg font-semibold tracking-tight">文脈で覚える</p>
            <p className="text-xs font-medium tracking-[0.18em] text-ink-muted">일본어 문맥 단어장</p>
          </div>
        </header>

        <div className="grid flex-1 items-center gap-12 py-12 lg:grid-cols-[1.05fr_0.95fr] lg:py-16">
          <section className="max-w-2xl">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-forest/15 bg-white/65 px-3 py-1.5 text-sm font-medium text-forest backdrop-blur">
              <span className="h-2 w-2 rounded-full bg-forest" />
              문장으로 익히는 오늘의 일본어
            </div>
            <h1 className="font-serif-jp text-4xl font-semibold leading-[1.18] tracking-[-0.035em] text-ink sm:text-5xl lg:text-6xl">
              단어가 아닌,
              <br />
              <span className="text-vermilion">문맥</span>을 기억하세요.
              <span className="sr-only"> Japanese Vocabulary Trainer</span>
            </h1>
            <p className="mt-6 max-w-xl text-base leading-7 text-ink-muted sm:text-lg sm:leading-8">
              새로운 일본어 예문에서 단어를 떠올리고, 기억한 정도에 맞춰 다음 복습을 준비합니다.
            </p>

            <div className="mt-9 grid max-w-xl gap-3 sm:grid-cols-3">
              {[
                ['01', '새 예문', '복습마다 달라지는 문맥'],
                ['02', '단어 살펴보기', '문장 속 읽기와 뜻'],
                ['03', '간격 복습', '기억에 맞춘 일정'],
              ].map(([number, title, detail]) => (
                <div key={number} className="rounded-2xl border border-line/80 bg-white/55 p-4 backdrop-blur-sm">
                  <span className="text-xs font-bold tracking-[0.16em] text-vermilion">{number}</span>
                  <p className="mt-2 font-semibold text-ink">{title}</p>
                  <p className="mt-1 text-xs leading-5 text-ink-muted">{detail}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="mx-auto w-full max-w-md rounded-[2rem] border border-white/80 bg-white/85 p-6 shadow-card backdrop-blur sm:p-8" aria-labelledby="login-title">
            <div className="mb-7">
              <span className="inline-block font-serif-jp text-3xl text-vermilion" aria-hidden="true">始</span>
              <h2 id="login-title" className="mt-3 text-2xl font-bold tracking-tight">학습을 시작해 볼까요?</h2>
              <p className="mt-2 text-sm leading-6 text-ink-muted">
                Google 계정으로 로그인하고, 내 Gemini API 키로 새 예문을 만들어요.
              </p>
            </div>

            {error && (
              <div className="mb-5 rounded-2xl border border-danger/20 bg-danger-soft p-4" role="alert" data-testid="auth-error">
                <p className="font-semibold text-danger">{error.title}</p>
                <p className="mt-1 text-sm leading-6 text-danger/80">{error.detail}</p>
              </div>
            )}

            <button
              type="button"
              onClick={handleLogin}
              disabled={starting}
              aria-busy={starting}
              aria-label="Google로 계속하기"
              className="group flex w-full items-center justify-center gap-3 rounded-2xl bg-ink px-5 py-3.5 font-semibold text-white shadow-soft transition hover:-translate-y-0.5 hover:bg-forest focus:outline-none focus:ring-4 focus:ring-forest/20 disabled:cursor-wait disabled:opacity-70"
            >
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-white" aria-hidden="true">
                <svg viewBox="0 0 24 24" className="h-4 w-4">
                  <path fill="#4285F4" d="M23.5 12.27c0-.85-.08-1.67-.22-2.45H12v4.64h6.45a5.52 5.52 0 0 1-2.4 3.62v3h3.88c2.27-2.09 3.57-5.17 3.57-8.81Z" />
                  <path fill="#34A853" d="M12 24c3.24 0 5.96-1.07 7.94-2.9l-3.88-3.01c-1.08.72-2.45 1.15-4.06 1.15-3.12 0-5.77-2.11-6.71-4.95H1.28v3.11A12 12 0 0 0 12 24Z" />
                  <path fill="#FBBC05" d="M5.29 14.29A7.2 7.2 0 0 1 4.91 12c0-.8.14-1.57.38-2.29V6.6H1.28A12 12 0 0 0 0 12c0 1.94.46 3.77 1.28 5.4l4.01-3.11Z" />
                  <path fill="#EA4335" d="M12 4.77c1.76 0 3.34.6 4.59 1.8l3.44-3.45C17.95 1.19 15.24 0 12 0A12 12 0 0 0 1.28 6.6l4.01 3.11C6.23 6.88 8.88 4.77 12 4.77Z" />
                </svg>
              </span>
              {starting ? 'Google로 이동하는 중…' : 'Google로 계속하기'}
              <span className="transition-transform group-hover:translate-x-1" aria-hidden="true">→</span>
            </button>

            <div className="mt-5 flex items-start gap-2 text-xs leading-5 text-ink-faint">
              <svg viewBox="0 0 24 24" className="mt-0.5 h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                <rect x="5" y="10" width="14" height="10" rx="2" />
                <path d="M8 10V7a4 4 0 0 1 8 0v3" />
              </svg>
              <p>Google 계정은 본인 확인에만 사용하며, Google 토큰은 저장하지 않습니다. 별도 비밀번호는 필요 없어요.</p>
            </div>
          </section>
        </div>
      </div>
    </main>
  )
}

export default Login

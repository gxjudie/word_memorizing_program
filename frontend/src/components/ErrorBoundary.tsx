import { Component, type ErrorInfo, type ReactNode } from 'react'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
}

/**
 * Keeps a render-time exception in one screen from blanking the whole app.
 *
 * The SPA has no router-level error handling, so before this boundary any
 * thrown error (for example a malformed sentence payload) unmounted everything
 * and left an empty page. The fallback offers a retry that remounts the screen
 * and a reload that starts from a clean state.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Kept in the console only: the message may contain learner content, which
    // must never be sent anywhere by this client.
    console.error('Unhandled UI error', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (error === null) return this.props.children

    return (
      <div className="mx-auto grid min-h-[60vh] max-w-xl place-items-center px-4 py-12 text-center">
        <section className="surface-card w-full p-8" role="alert">
          <span className="font-serif-jp text-4xl text-danger" aria-hidden="true">!</span>
          <h2 className="mt-3 text-2xl font-bold">화면을 표시하지 못했어요</h2>
          <p className="mt-2 text-sm leading-6 text-ink-muted">
            예상하지 못한 문제가 생겼습니다. 다시 시도해 보고, 계속되면 페이지를 새로 고쳐 주세요.
          </p>
          <div className="mt-6 flex justify-center gap-2">
            <button
              type="button"
              onClick={() => this.setState({ error: null })}
              className="rounded-2xl bg-ink px-5 py-3 text-sm font-semibold text-white transition hover:bg-forest focus:outline-none focus:ring-4 focus:ring-forest/20"
            >
              다시 시도
            </button>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-2xl border border-line bg-white px-5 py-3 text-sm font-semibold text-ink-muted transition hover:text-ink focus:outline-none focus:ring-4 focus:ring-forest/15"
            >
              새로 고침
            </button>
          </div>
        </section>
      </div>
    )
  }
}

export default ErrorBoundary

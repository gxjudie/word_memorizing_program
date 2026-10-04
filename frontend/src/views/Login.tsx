import { useState } from 'react'
import { login } from '../api/endpoints'
import { ApiError } from '../api/types'

export interface LoginProps {
  onLoggedIn: () => void
}

/** Minimal login form posting to the backend auth route. */
export function Login({ onLoggedIn }: LoginProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await login(email, password)
      onLoggedIn()
    } catch (err) {
      setError(
        err instanceof ApiError && err.kind === 'unauthorized'
          ? 'Invalid email or password.'
          : err instanceof ApiError
            ? err.message
            : 'Login failed.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto max-w-sm space-y-4 p-6">
      <h2 className="text-xl font-semibold">Log in</h2>
      <div>
        <label htmlFor="email" className="block text-sm font-medium">
          Email
        </label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
        />
      </div>
      <div>
        <label htmlFor="password" className="block text-sm font-medium">
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
        />
      </div>
      {error && (
        <p role="alert" className="text-sm text-rose-600">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={submitting}
        className="rounded bg-indigo-600 px-4 py-2 font-medium text-white disabled:opacity-50"
      >
        {submitting ? 'Logging in…' : 'Log in'}
      </button>
    </form>
  )
}

export default Login

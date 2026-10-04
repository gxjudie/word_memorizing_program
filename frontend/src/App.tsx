import { useState } from 'react'
import AddWord from './views/AddWord'
import Login from './views/Login'
import ReviewSession from './views/ReviewSession'
import { getToken, setToken } from './api/client'
import { logout } from './api/endpoints'

type Tab = 'add' | 'review'

export default function App() {
  const [authed, setAuthed] = useState<boolean>(() => getToken() !== null)
  const [tab, setTab] = useState<Tab>('review')

  if (!authed) {
    return (
      <main className="min-h-screen bg-slate-50 text-slate-900">
        <h1 className="p-6 text-2xl font-bold">Japanese Vocabulary Trainer</h1>
        <Login onLoggedIn={() => setAuthed(true)} />
      </main>
    )
  }

  async function handleLogout() {
    await logout()
    setToken(null)
    setAuthed(false)
  }

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-4">
        <h1 className="text-2xl font-bold">Japanese Vocabulary Trainer</h1>
        <nav className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setTab('review')}
            className={
              'rounded px-3 py-1.5 text-sm ' +
              (tab === 'review' ? 'bg-indigo-600 text-white' : 'bg-slate-100')
            }
          >
            Review
          </button>
          <button
            type="button"
            onClick={() => setTab('add')}
            className={
              'rounded px-3 py-1.5 text-sm ' +
              (tab === 'add' ? 'bg-indigo-600 text-white' : 'bg-slate-100')
            }
          >
            Add word
          </button>
          <button
            type="button"
            onClick={() => void handleLogout()}
            className="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
          >
            Log out
          </button>
        </nav>
      </header>

      {tab === 'review' ? <ReviewSession /> : <div className="p-6"><AddWord /></div>}
    </main>
  )
}

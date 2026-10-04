import type { SentenceToken } from '../api/types'

export interface LookupPanelProps {
  /** The most recently selected non-target token, or null when none/dismissed. */
  token: SentenceToken | null
  /** Dismiss the panel, clearing its content (Req 5.5). */
  onDismiss?: () => void
}

/**
 * Shows the reading and meaning of the most recently selected Non_Target_Word
 * (Req 5).
 *
 * - Shows the token's `meaning` and `reading`, plus furigana (the reading) when
 *   the token `contains_kanji` (Req 5.1, 5.2).
 * - A new selection replaces the content (the parent passes the latest token),
 *   so the panel reflects only the most recent selection (Req 5.3).
 * - When the token's `meaning` is unavailable (null), the panel shows an
 *   unavailable message and no stale meaning (Req 5.4).
 * - Content persists until another selection or dismissal (Req 5.5).
 */
export function LookupPanel({ token, onDismiss }: LookupPanelProps) {
  if (token === null) {
    return (
      <aside
        className="rounded border border-slate-200 bg-slate-50 p-4 text-slate-400"
        data-testid="lookup-panel"
        aria-live="polite"
      >
        Select a word to see its reading and meaning.
      </aside>
    )
  }

  const meaningAvailable = token.meaning !== null && token.meaning !== ''

  return (
    <aside
      className="rounded border border-slate-200 bg-white p-4 shadow-sm"
      data-testid="lookup-panel"
      aria-live="polite"
    >
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xl font-semibold" data-testid="lookup-surface">
            {token.surface}
          </div>
          <div className="text-slate-600" data-testid="lookup-reading">
            {token.reading}
          </div>
          {token.contains_kanji && (
            <div
              className="text-sm text-slate-500"
              data-testid="lookup-furigana"
            >
              Furigana: {token.reading}
            </div>
          )}
          {meaningAvailable ? (
            <div className="mt-2" data-testid="lookup-meaning">
              {token.meaning}
            </div>
          ) : (
            <div
              className="mt-2 italic text-slate-500"
              data-testid="lookup-unavailable"
            >
              Meaning unavailable.
            </div>
          )}
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="text-slate-400 hover:text-slate-700"
            aria-label="Dismiss lookup"
          >
            ✕
          </button>
        )}
      </div>
    </aside>
  )
}

export default LookupPanel

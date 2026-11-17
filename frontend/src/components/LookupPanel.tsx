import type { SentenceToken } from '../api/types'

export interface LookupPanelProps {
  token: SentenceToken | null
  onDismiss?: () => void
}

/** Show only the latest selected non-target token until dismissed. */
export function LookupPanel({ token, onDismiss }: LookupPanelProps) {
  if (token === null) {
    return (
      <aside
        className="rounded-2xl border border-dashed border-line bg-white/35 px-4 py-3.5 text-sm text-ink-faint"
        data-testid="lookup-panel"
        aria-live="polite"
      >
        <span className="mr-2 font-serif-jp text-forest" aria-hidden="true">辞</span>
        문장에서 궁금한 단어를 선택해 보세요.
      </aside>
    )
  }

  const meaningAvailable = token.meaning !== null && token.meaning !== ''

  return (
    <aside
      className="relative overflow-hidden rounded-2xl border border-forest/15 bg-forest-soft/70 px-5 py-4"
      data-testid="lookup-panel"
      aria-live="polite"
    >
      <div className="absolute inset-y-0 left-0 w-1 bg-forest" />
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 sm:flex sm:items-center sm:gap-5">
          <div className="shrink-0">
            <div className="font-serif-jp text-2xl font-semibold text-forest" data-testid="lookup-surface" lang="ja">
              {token.surface}
            </div>
            <div className="mt-0.5 text-sm text-ink-muted" data-testid="lookup-reading">
              {token.reading}
            </div>
          </div>
          <div className="mt-3 border-t border-forest/10 pt-3 sm:mt-0 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0">
            {token.contains_kanji && (
              <div className="mb-1 text-xs font-medium text-ink-faint" data-testid="lookup-furigana">
                후리가나 · {token.reading}
              </div>
            )}
            {meaningAvailable ? (
              <div className="font-medium text-ink" data-testid="lookup-meaning">{token.meaning}</div>
            ) : (
              <div className="text-sm italic text-ink-muted" data-testid="lookup-unavailable">확인할 수 있는 뜻이 없어요.</div>
            )}
          </div>
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-ink-faint transition hover:bg-white/70 hover:text-ink focus:outline-none focus:ring-4 focus:ring-forest/15"
            aria-label="Dismiss lookup"
          >
            <span aria-hidden="true">×</span>
          </button>
        )}
      </div>
    </aside>
  )
}

export default LookupPanel

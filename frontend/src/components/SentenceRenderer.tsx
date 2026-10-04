import { useState } from 'react'
import type { GeneratedSentence, SentenceToken } from '../api/types'

export interface SentenceRendererProps {
  /** The generated sentence to render, or null when none is available. */
  sentence: GeneratedSentence | null
  /** Called when a non-target token is clicked (drives the Lookup Panel). */
  onSelectToken?: (token: SentenceToken, index: number) => void
}

/**
 * Renders an Example_Sentence from its structured token array (Req 3).
 *
 * Each token is an interactive span. Target tokens (`is_target`) get an
 * emphasis style that differs from non-target tokens in at least one
 * observable attribute — color, weight, and background (Req 3.2). A per-sentence
 * "show furigana" toggle renders each token's reading in parentheses (Req 3.1).
 * When no sentence is present, the "no example available" state is shown (3.5).
 */
export function SentenceRenderer({
  sentence,
  onSelectToken,
}: SentenceRendererProps) {
  const [showFurigana, setShowFurigana] = useState(false)

  if (sentence === null) {
    return (
      <p className="text-slate-500 italic" data-testid="no-example">
        No example sentence available.
      </p>
    )
  }

  return (
    <div>
      <div className="mb-2 flex justify-end">
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={showFurigana}
            onChange={(e) => setShowFurigana(e.target.checked)}
            aria-label="Show furigana"
          />
          Show furigana
        </label>
      </div>
      <p className="text-2xl leading-relaxed" data-testid="sentence">
        {sentence.tokens.map((token, index) => {
          const isTarget = token.is_target
          // Target emphasis differs in color, weight, AND background (Req 3.2).
          const className = isTarget
            ? 'text-rose-700 font-bold bg-rose-100 rounded px-0.5'
            : 'text-slate-900 font-normal bg-transparent hover:bg-slate-100 cursor-pointer rounded px-0.5'
          return (
            <span
              key={index}
              data-testid={`token-${index}`}
              data-is-target={isTarget}
              className={className}
              role={isTarget ? undefined : 'button'}
              tabIndex={isTarget ? undefined : 0}
              onClick={
                isTarget
                  ? undefined
                  : () => onSelectToken?.(token, index)
              }
              onKeyDown={
                isTarget
                  ? undefined
                  : (e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        onSelectToken?.(token, index)
                      }
                    }
              }
            >
              {token.surface}
              {showFurigana && (
                <span className="text-sm text-slate-500">
                  ({token.reading})
                </span>
              )}
            </span>
          )
        })}
      </p>
    </div>
  )
}

export default SentenceRenderer

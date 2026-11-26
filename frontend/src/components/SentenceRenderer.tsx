import { useState } from 'react'
import type { GeneratedSentence, SentenceToken } from '../api/types'

export interface SentenceRendererProps {
  sentence: GeneratedSentence | null
  onSelectToken?: (token: SentenceToken, index: number) => void
}

/** Render a tokenized sentence with target emphasis and optional readings. */
export function SentenceRenderer({ sentence, onSelectToken }: SentenceRendererProps) {
  const [showFurigana, setShowFurigana] = useState(false)

  if (sentence === null) {
    return (
      <div className="grid min-h-48 place-items-center text-center" data-testid="no-example">
        <div>
          <span className="font-serif-jp text-3xl text-ink-faint" aria-hidden="true">文</span>
          <p className="mt-2 font-medium text-ink-muted">표시할 예문이 없어요.</p>
          <p className="mt-1 text-sm text-ink-faint">단어를 먼저 확인한 뒤 복습을 이어가세요.</p>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="mb-8 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-bold tracking-[0.17em] text-vermilion">CONTEXT</p>
          <p className="mt-1 text-sm text-ink-muted">강조된 단어의 뜻을 문맥에서 떠올려 보세요.</p>
        </div>
        <label className="flex cursor-pointer items-center gap-2.5 rounded-full border border-line bg-washi px-3 py-2 text-sm font-medium text-ink-muted transition hover:border-forest/30">
          <input
            type="checkbox"
            checked={showFurigana}
            onChange={(event) => setShowFurigana(event.target.checked)}
            className="peer sr-only"
          />
          <span className="relative h-5 w-9 rounded-full bg-line transition peer-checked:bg-forest peer-focus-visible:ring-4 peer-focus-visible:ring-forest/20 after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:shadow-sm after:transition after:content-[''] peer-checked:after:translate-x-4" aria-hidden="true" />
          후리가나
        </label>
      </div>

      <p className="font-serif-jp text-[1.75rem] leading-[2.1] tracking-wide text-ink sm:text-4xl sm:leading-[2]" data-testid="sentence" lang="ja">
        {sentence.tokens.map((token, index) => {
          const isTarget = token.is_target
          const content = (
            <>
              {token.surface}
              {showFurigana && (
                <span className="ml-0.5 align-middle font-sans text-xs font-medium tracking-normal text-ink-faint sm:text-sm">
                  ({token.reading})
                </span>
              )}
            </>
          )
          // Non-target tokens are real buttons, not spans with role="button":
          // they are interactive, so they belong in the tab order and the
          // accessibility tree natively (Enter/Space work without extra code).
          return isTarget ? (
            <span
              key={`${token.surface}-${index}`}
              data-testid={`token-${index}`}
              data-is-target="true"
              className="relative mx-0.5 rounded-lg bg-vermilion-soft px-1.5 py-1 font-bold text-vermilion shadow-[inset_0_-2px_0_rgba(173,75,43,0.28)]"
            >
              {content}
            </span>
          ) : (
            <button
              key={`${token.surface}-${index}`}
              type="button"
              data-testid={`token-${index}`}
              data-is-target="false"
              aria-label={`${token.surface} 뜻 보기`}
              onClick={() => onSelectToken?.(token, index)}
              className="mx-0.5 rounded-lg bg-transparent px-1 py-1 font-normal text-ink transition hover:bg-forest-soft hover:text-forest focus:outline-none focus:ring-4 focus:ring-forest/15"
            >
              {content}
            </button>
          )
        })}
      </p>

      <p className="mt-7 flex items-center gap-2 border-t border-line/70 pt-4 text-xs text-ink-faint">
        <span className="grid h-5 w-5 place-items-center rounded-full bg-forest-soft text-forest" aria-hidden="true">?</span>
        모르는 단어를 누르면 읽기와 뜻을 확인할 수 있어요.
      </p>
    </div>
  )
}

export default SentenceRenderer

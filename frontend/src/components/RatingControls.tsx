import {
  DIFFICULTY_LABELS,
  DIFFICULTY_ORDER,
  Difficulty,
  type WordEntryOut,
} from '../api/types'

export interface RatingControlsProps {
  word: WordEntryOut
  revealed: boolean
  selectedRating?: Difficulty | null
  onReveal: () => void
  onRate: (rating: Difficulty) => void
  errorMessage?: string | null
}

const KOREAN_LABELS: Record<Difficulty, string> = {
  [Difficulty.VeryHard]: '매우 어려움',
  [Difficulty.Hard]: '어려움',
  [Difficulty.Good]: '기억남',
  [Difficulty.VeryEasy]: '매우 쉬움',
}

const INTERVAL_HINTS: Record<Difficulty, string> = {
  [Difficulty.VeryHard]: '곧 다시',
  [Difficulty.Hard]: '짧게',
  [Difficulty.Good]: '적당히',
  [Difficulty.VeryEasy]: '여유롭게',
}

/** Reveal the answer, then offer exactly four ordered recall ratings. */
export function RatingControls({
  word,
  revealed,
  selectedRating,
  onReveal,
  onRate,
  errorMessage,
}: RatingControlsProps) {
  return (
    <section className="mt-5" aria-label="정답과 기억 평가">
      {!revealed ? (
        <div className="rounded-3xl border border-line/80 bg-white/60 p-4 text-center sm:p-5">
          <p className="mb-3 text-sm text-ink-muted">문맥에서 단어가 떠올랐나요?</p>
          <button
            type="button"
            onClick={onReveal}
            className="w-full rounded-2xl bg-ink px-5 py-3.5 font-semibold text-white shadow-soft transition hover:-translate-y-0.5 hover:bg-forest focus:outline-none focus:ring-4 focus:ring-forest/20 sm:w-auto sm:min-w-56"
            data-testid="reveal-button"
            aria-label="Reveal answer"
          >
            정답 확인하기
          </button>
        </div>
      ) : (
        <div>
          <div className="relative overflow-hidden rounded-3xl border border-vermilion/15 bg-vermilion-soft/75 p-5 sm:p-6" data-testid="answer">
            <div className="absolute right-5 top-3 font-serif-jp text-5xl text-vermilion/10" aria-hidden="true">答</div>
            <p className="text-xs font-bold tracking-[0.16em] text-vermilion">ANSWER</p>
            <div className="mt-3 font-serif-jp text-lg text-ink-muted" data-testid="answer-reading" lang="ja">{word.reading}</div>
            <div className="mt-1 text-xl font-bold text-ink sm:text-2xl" data-testid="answer-meaning">{word.meaning}</div>
          </div>

          <div className="mt-6">
            <div className="mb-3 flex items-end justify-between gap-3">
              <div>
                <p className="font-semibold text-ink">얼마나 잘 기억했나요?</p>
                <p className="mt-1 text-xs text-ink-faint">응답에 따라 다음 복습 간격이 달라져요.</p>
              </div>
              <span className="hidden text-xs text-ink-faint sm:block">어려움 → 쉬움</span>
            </div>
            <div
              className="grid grid-cols-2 gap-2 sm:grid-cols-4"
              role="group"
              aria-label="Difficulty rating"
              data-testid="rating-options"
            >
              {DIFFICULTY_ORDER.map((level) => {
                const isSelected = selectedRating === level
                return (
                  <button
                    key={level}
                    type="button"
                    onClick={() => onRate(level)}
                    aria-pressed={isSelected}
                    aria-label={`${level}. ${DIFFICULTY_LABELS[level]}`}
                    data-testid={`rating-${level}`}
                    data-selected={isSelected}
                    className={
                      'rounded-2xl border px-3 py-3 text-left transition focus:outline-none focus:ring-4 focus:ring-forest/15 ' +
                      (isSelected
                        ? 'border-forest bg-forest text-white shadow-soft'
                        : 'border-line bg-white text-ink hover:-translate-y-0.5 hover:border-forest/30 hover:bg-forest-soft/50')
                    }
                  >
                    <span className={`block text-sm font-bold ${isSelected ? 'text-white' : 'text-ink'}`}>{KOREAN_LABELS[level]}</span>
                    <span className={`mt-1 block text-[11px] ${isSelected ? 'text-white/70' : 'text-ink-faint'}`}>{level} · {INTERVAL_HINTS[level]}</span>
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {errorMessage && (
        <p className="mt-4 rounded-2xl border border-danger/15 bg-danger-soft px-4 py-3 text-sm font-medium text-danger" role="alert" data-testid="rating-error">
          {errorMessage}
        </p>
      )}
    </section>
  )
}

export default RatingControls

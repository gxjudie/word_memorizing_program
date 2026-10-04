import {
  DIFFICULTY_LABELS,
  DIFFICULTY_ORDER,
  type Difficulty,
  type WordEntryOut,
} from '../api/types'

export interface RatingControlsProps {
  /** The word under review (its reading/meaning are shown after reveal). */
  word: WordEntryOut
  /** Whether the answer has been revealed (Req 6.3 gating). */
  revealed: boolean
  /** The rating already selected for this word, if any (marks selection). */
  selectedRating?: Difficulty | null
  /** Reveal the Target_Word reading + meaning (Req 6.1). */
  onReveal: () => void
  /** Record the chosen difficulty level (Req 6.4). */
  onRate: (rating: Difficulty) => void
  /** An error message to surface (e.g. "rating not saved"), if any. */
  errorMessage?: string | null
}

/**
 * Reveal + rating controls (Req 3.3, 3.4, 6).
 *
 * Before reveal, only the reveal control is shown; the Target_Word meaning is
 * withheld and no rating option is offered (Req 6.3). Revealing shows the
 * reading and meaning (Req 6.1). After reveal, exactly four rating options are
 * offered in order from very hard (1) to very easy (4) (Req 6.2); selecting one
 * records that level and marks it selected (Req 6.4). Rating/invalid errors are
 * surfaced for the Learner (Req 6.6, 8.6).
 */
export function RatingControls({
  word,
  revealed,
  selectedRating,
  onReveal,
  onRate,
  errorMessage,
}: RatingControlsProps) {
  return (
    <div className="mt-4">
      {!revealed ? (
        <button
          type="button"
          onClick={onReveal}
          className="rounded bg-slate-800 px-4 py-2 font-medium text-white hover:bg-slate-700"
          data-testid="reveal-button"
        >
          Reveal answer
        </button>
      ) : (
        <div>
          <div
            className="mb-3 rounded bg-slate-50 p-3"
            data-testid="answer"
          >
            <div className="text-slate-600" data-testid="answer-reading">
              {word.reading}
            </div>
            <div className="text-lg" data-testid="answer-meaning">
              {word.meaning}
            </div>
          </div>

          <p className="mb-2 text-sm text-slate-600">
            How well did you recall it?
          </p>
          <div
            className="flex flex-wrap gap-2"
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
                  data-testid={`rating-${level}`}
                  data-selected={isSelected}
                  className={
                    'rounded border px-3 py-2 text-sm font-medium ' +
                    (isSelected
                      ? 'border-indigo-600 bg-indigo-600 text-white'
                      : 'border-slate-300 bg-white text-slate-800 hover:bg-slate-100')
                  }
                >
                  {level}. {DIFFICULTY_LABELS[level]}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {errorMessage && (
        <p className="mt-3 text-sm text-rose-600" data-testid="rating-error">
          {errorMessage}
        </p>
      )}
    </div>
  )
}

export default RatingControls

import { useCallback, useEffect, useState } from 'react'
import LookupPanel from '../components/LookupPanel'
import RatingControls from '../components/RatingControls'
import SentenceRenderer from '../components/SentenceRenderer'
import {
  generateSentence,
  rateWord,
  revealWord,
  startSession,
} from '../api/endpoints'
import {
  ApiError,
  isNoDueWords,
  type Difficulty,
  type GeneratedSentence,
  type RatingResult,
  type SentenceToken,
  type SessionSummary,
  type WordEntryOut,
} from '../api/types'

type Phase = 'loading' | 'no-due' | 'reviewing' | 'complete' | 'error'

/**
 * Review Session view — orchestrates the session loop (Req 8).
 *
 * Starts a session (showing "no words due" when empty, Req 8.5), presents one
 * Target_Word at a time (Req 8.2), requests a fresh sentence with a retry
 * control on generation error (Req 4.5), composes the Sentence Renderer +
 * Lookup Panel + Rating controls, advances on rating (Req 8.3), and renders the
 * end-of-session summary (Req 8.4).
 */
export function ReviewSession() {
  const [phase, setPhase] = useState<Phase>('loading')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [currentWord, setCurrentWord] = useState<WordEntryOut | null>(null)
  const [sentence, setSentence] = useState<GeneratedSentence | null>(null)
  const [sentenceLoading, setSentenceLoading] = useState(false)
  const [generationError, setGenerationError] = useState<string | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [selectedToken, setSelectedToken] = useState<SentenceToken | null>(null)
  const [ratingError, setRatingError] = useState<string | null>(null)
  const [summary, setSummary] = useState<SessionSummary | null>(null)
  const [fatalError, setFatalError] = useState<string | null>(null)

  const loadSentence = useCallback(
    async (sid: string, word: WordEntryOut) => {
      setSentenceLoading(true)
      setGenerationError(null)
      setSentence(null)
      try {
        const gen = await generateSentence(sid, word.id)
        setSentence(gen)
      } catch (err) {
        if (
          err instanceof ApiError &&
          (err.kind === 'generation_timeout' ||
            err.kind === 'generation_failed')
        ) {
          // Keep the Target_Word in the session and offer retry (Req 4.5).
          setGenerationError(err.message)
        } else {
          setGenerationError(
            err instanceof ApiError ? err.message : 'Failed to load a sentence.',
          )
        }
      } finally {
        setSentenceLoading(false)
      }
    },
    [],
  )

  const presentWord = useCallback(
    (sid: string, word: WordEntryOut) => {
      setCurrentWord(word)
      setRevealed(false)
      setSelectedToken(null)
      setRatingError(null)
      void loadSentence(sid, word)
    },
    [loadSentence],
  )

  const begin = useCallback(async () => {
    setPhase('loading')
    setFatalError(null)
    try {
      const res = await startSession()
      if (isNoDueWords(res)) {
        setPhase('no-due')
        return
      }
      setSessionId(res.session_id)
      setPhase('reviewing')
      presentWord(res.session_id, res.due_words[0])
    } catch (err) {
      setPhase('error')
      setFatalError(
        err instanceof ApiError ? err.message : 'Could not start a session.',
      )
    }
  }, [presentWord])

  useEffect(() => {
    void begin()
  }, [begin])

  async function handleReveal() {
    if (!sessionId || !currentWord) return
    try {
      await revealWord(sessionId, currentWord.id)
      setRevealed(true)
    } catch (err) {
      setRatingError(
        err instanceof ApiError ? err.message : 'Could not reveal the answer.',
      )
    }
  }

  function applyResult(result: RatingResult) {
    if (result.session_complete) {
      setSummary(result.summary)
      setPhase('complete')
    } else if (result.next_word && sessionId) {
      presentWord(sessionId, result.next_word)
    }
  }

  async function handleRate(rating: Difficulty) {
    if (!sessionId || !currentWord) return
    setRatingError(null)
    try {
      const result = await rateWord(sessionId, currentWord.id, rating)
      applyResult(result)
    } catch (err) {
      if (err instanceof ApiError && err.kind === 'scheduling_not_saved') {
        setRatingError('Rating not saved. Please try again.')
      } else if (err instanceof ApiError && err.kind === 'invalid_rating') {
        setRatingError('That rating is not valid.')
      } else if (err instanceof ApiError && err.kind === 'not_revealed') {
        setRatingError('Reveal the answer before rating.')
      } else {
        setRatingError(
          err instanceof ApiError ? err.message : 'Could not record the rating.',
        )
      }
    }
  }

  if (phase === 'loading') {
    return <p className="p-6 text-slate-500">Starting review session…</p>
  }

  if (phase === 'error') {
    return (
      <div className="p-6">
        <p className="text-rose-600" data-testid="session-error">
          {fatalError}
        </p>
        <button
          type="button"
          onClick={() => void begin()}
          className="mt-3 rounded bg-slate-800 px-4 py-2 text-white"
        >
          Try again
        </button>
      </div>
    )
  }

  if (phase === 'no-due') {
    return (
      <p className="p-6 text-lg" data-testid="no-due">
        No words are due for review today. 🎉
      </p>
    )
  }

  if (phase === 'complete') {
    return (
      <div className="p-6" data-testid="summary">
        <h2 className="text-2xl font-bold">Session complete</h2>
        <p className="mt-2">
          Total reviewed:{' '}
          <span data-testid="summary-total">{summary?.total_reviewed ?? 0}</span>
        </p>
        {summary && (
          <ul className="mt-2 list-inside list-disc text-slate-700">
            {Object.entries(summary.counts_by_rating).map(([level, count]) => (
              <li key={level} data-testid={`summary-count-${level}`}>
                Level {level}: {count}
              </li>
            ))}
          </ul>
        )}
        <button
          type="button"
          onClick={() => void begin()}
          className="mt-4 rounded bg-indigo-600 px-4 py-2 text-white"
        >
          Review again
        </button>
      </div>
    )
  }

  // phase === 'reviewing'
  return (
    <div className="mx-auto max-w-2xl p-6">
      <div className="mb-4 rounded border border-slate-200 bg-white p-4 shadow-sm">
        {sentenceLoading && (
          <p className="text-slate-500" data-testid="sentence-loading">
            Generating a sentence…
          </p>
        )}
        {!sentenceLoading && generationError && (
          <div data-testid="generation-error">
            <p className="text-rose-600">{generationError}</p>
            <button
              type="button"
              onClick={() =>
                sessionId &&
                currentWord &&
                void loadSentence(sessionId, currentWord)
              }
              className="mt-2 rounded bg-slate-800 px-3 py-1.5 text-sm text-white"
              data-testid="retry-generation"
            >
              Retry
            </button>
          </div>
        )}
        {!sentenceLoading && !generationError && (
          <SentenceRenderer
            sentence={sentence}
            onSelectToken={(token) => setSelectedToken(token)}
          />
        )}
      </div>

      <LookupPanel
        token={selectedToken}
        onDismiss={() => setSelectedToken(null)}
      />

      {currentWord && (
        <RatingControls
          word={currentWord}
          revealed={revealed}
          onReveal={() => void handleReveal()}
          onRate={(r) => void handleRate(r)}
          errorMessage={ratingError}
        />
      )}
    </div>
  )
}

export default ReviewSession

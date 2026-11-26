import { useCallback, useEffect, useRef, useState } from 'react'
import Dialog from '../components/Dialog'
import LookupPanel from '../components/LookupPanel'
import RatingControls from '../components/RatingControls'
import SentenceRenderer from '../components/SentenceRenderer'
import GeminiKeySetup from './GeminiKeySetup'
import { generateSentence, rateWord, revealWord, startSession } from '../api/endpoints'
import {
  ApiError,
  Difficulty,
  isNoDueWords,
  type ApiErrorKind,
  type GeminiKeyStatus,
  type DueWord,
  type GeneratedSentence,
  type RatingResult,
  type RevealedWord,
  type SentenceToken,
  type SessionSummary,
} from '../api/types'

type Phase = 'loading' | 'no-due' | 'reviewing' | 'complete' | 'error' | 'key-required'
type GenerationIssue = { kind: ApiErrorKind; title: string; detail: string }
type KeyDialog = { mode: 'setup' | 'replace' } | null

const SUMMARY_LABELS: Record<string, string> = {
  '1': '매우 어려움',
  '2': '어려움',
  '3': '기억남',
  '4': '매우 쉬움',
}

/** Digit shortcuts for the four ratings, in presentation order (1..4). */
const RATING_KEYS: Record<string, Difficulty> = {
  '1': 1,
  '2': 2,
  '3': 3,
  '4': 4,
}

const NOT_CONNECTED: GeminiKeyStatus = { connected: false, key_hint: null }

const SESSION_EXPIRED_MESSAGE =
  '복습 세션이 만료되었어요. 오래 열어 둔 화면이라 처음부터 다시 시작합니다.'

function isSessionExpired(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'session_expired'
}


/** Friendly Korean copy per sentence-generation failure (Req 4.5, 4.7–4.11). */
function generationIssue(error: unknown): GenerationIssue {
  const kind: ApiErrorKind = error instanceof ApiError ? error.code : 'unknown'
  switch (kind) {
    case 'gemini_key_required':
      return {
        kind,
        title: 'Gemini API 키를 설정하세요',
        detail: '예문을 만들려면 내 Gemini API 키가 필요해요. 키를 연결하면 이 단어의 예문을 바로 다시 만들어 드릴게요.',
      }
    case 'invalid_gemini_key':
      return {
        kind,
        title: '연결된 Gemini API 키를 쓸 수 없어요',
        detail: '키가 삭제되었거나 더 이상 유효하지 않아요. 새 키로 바꾸면 이 단어의 예문을 다시 만들어 드릴게요.',
      }
    case 'gemini_quota_exceeded':
      return {
        kind,
        title: 'Gemini 사용량 한도에 도달했어요',
        detail: '무료 등급은 분당·일일 사용량이 정해져 있어요. 1분쯤 기다렸다가 다시 시도해 보세요. 일일 한도라면 내일 다시 만들 수 있어요.',
      }
    case 'generation_timeout':
      return {
        kind,
        title: '예문을 만드는 데 시간이 너무 걸렸어요',
        detail: '응답이 늦어 이번 예문은 건너뛰었어요. 다시 시도해 주세요.',
      }
    default:
      return {
        kind,
        title: '예문을 만들지 못했어요',
        detail: '예문 생성 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요.',
      }
  }
}

export interface ReviewSessionProps {
  /** Keeps the header's key indicator in sync with key changes made here. */
  onKeyStatusChange?: (status: GeminiKeyStatus) => void
  /** "나중에" from the key guide: go add words instead (Req 10.2). */
  onLater?: () => void
  /** "단어장 보기" from the no-due screen. */
  onOpenWords?: () => void
}

/** Orchestrate one-at-a-time due-word review without exposing provider tokens. */
export function ReviewSession({
  onKeyStatusChange,
  onLater,
  onOpenWords,
}: ReviewSessionProps = {}) {
  const [phase, setPhase] = useState<Phase>('loading')
  const [keyDialog, setKeyDialog] = useState<KeyDialog>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [currentWord, setCurrentWord] = useState<DueWord | null>(null)
  const [answer, setAnswer] = useState<RevealedWord | null>(null)
  const [sentence, setSentence] = useState<GeneratedSentence | null>(null)
  const [sentenceLoading, setSentenceLoading] = useState(false)
  const [generationError, setGenerationError] = useState<GenerationIssue | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [selectedRating, setSelectedRating] = useState<Difficulty | null>(null)
  const [selectedToken, setSelectedToken] = useState<SentenceToken | null>(null)
  const [ratingError, setRatingError] = useState<string | null>(null)
  const [summary, setSummary] = useState<SessionSummary | null>(null)
  const [fatalError, setFatalError] = useState<string | null>(null)
  const [progress, setProgress] = useState({ current: 1, total: 1 })

  // Keep the callback in a ref so a new parent closure never restarts the session.
  const keyStatusListener = useRef(onKeyStatusChange)
  keyStatusListener.current = onKeyStatusChange
  const notifyKeyStatus = useCallback((status: GeminiKeyStatus) => {
    keyStatusListener.current?.(status)
  }, [])

  const loadSentence = useCallback(async (sid: string, word: DueWord) => {
    setSentenceLoading(true)
    setGenerationError(null)
    setSentence(null)
    try {
      setSentence(await generateSentence(sid, word.id))
    } catch (error) {
      if (isSessionExpired(error)) {
        setPhase('error')
        setFatalError(SESSION_EXPIRED_MESSAGE)
        return
      }
      const issue = generationIssue(error)
      if (issue.kind === 'gemini_key_required') notifyKeyStatus(NOT_CONNECTED)
      setGenerationError(issue)
    } finally {
      setSentenceLoading(false)
    }
  }, [notifyKeyStatus])

  const presentWord = useCallback((sid: string, word: DueWord) => {
    setCurrentWord(word)
    setAnswer(null)
    setRevealed(false)
    setSelectedRating(null)
    setSelectedToken(null)
    setRatingError(null)
    void loadSentence(sid, word)
  }, [loadSentence])

  const begin = useCallback(async () => {
    setPhase('loading')
    setFatalError(null)
    setSummary(null)
    try {
      const response = await startSession()
      if (isNoDueWords(response)) {
        setPhase('no-due')
        return
      }
      setSessionId(response.session_id)
      setProgress({ current: 1, total: response.due_words.length })
      setPhase('reviewing')
      presentWord(response.session_id, response.due_words[0])
    } catch (error) {
      // No stored key: present the Key_Setup_Guide instead of a session (Req 10.1).
      if (error instanceof ApiError && error.code === 'gemini_key_required') {
        notifyKeyStatus(NOT_CONNECTED)
        setPhase('key-required')
        return
      }
      setPhase('error')
      setFatalError(error instanceof ApiError ? error.message : '복습을 시작하지 못했어요.')
    }
  }, [presentWord, notifyKeyStatus])

  /** A key was connected from the in-session dialog: retry the same word. */
  function handleDialogConnected() {
    setKeyDialog(null)
    if (sessionId && currentWord) void loadSentence(sessionId, currentWord)
  }

  useEffect(() => {
    void begin()
  }, [begin])

  async function handleReveal() {
    if (!sessionId || !currentWord) return
    try {
      setAnswer(await revealWord(sessionId, currentWord.id))
      setRevealed(true)
    } catch (error) {
      if (isSessionExpired(error)) {
        setPhase('error')
        setFatalError(SESSION_EXPIRED_MESSAGE)
        return
      }
      setRatingError(error instanceof ApiError ? error.message : '정답을 불러오지 못했어요.')
    }
  }

  function applyResult(result: RatingResult) {
    if (result.session_complete) {
      setSummary(result.summary)
      setPhase('complete')
    } else if (result.next_word && sessionId) {
      setProgress((value) => ({ ...value, current: value.current + 1 }))
      presentWord(sessionId, result.next_word)
    } else {
      // Defensive: a non-final rating without a next word would otherwise leave
      // the same card on screen with no way forward.
      setSelectedRating(null)
      setRatingError('다음 단어를 불러오지 못했어요. 화면을 새로 고쳐 주세요.')
    }
  }

  async function handleRate(rating: Difficulty) {
    if (!sessionId || !currentWord) return
    setSelectedRating(rating)
    setRatingError(null)
    try {
      applyResult(await rateWord(sessionId, currentWord.id, rating))
    } catch (error) {
      setSelectedRating(null)
      if (isSessionExpired(error)) {
        setPhase('error')
        setFatalError(SESSION_EXPIRED_MESSAGE)
      } else if (error instanceof ApiError && error.kind === 'scheduling_not_saved') {
        setRatingError('평가를 저장하지 못했어요. 다시 선택해 주세요.')
      } else if (error instanceof ApiError && error.kind === 'invalid_rating') {
        setRatingError('선택한 평가가 올바르지 않아요.')
      } else if (error instanceof ApiError && error.kind === 'not_revealed') {
        setRatingError('평가하기 전에 정답을 확인해 주세요.')
      } else {
        setRatingError(error instanceof ApiError ? error.message : '평가를 기록하지 못했어요.')
      }
    }
  }

  const ratingPending = selectedRating !== null

  // Keyboard-first review, Anki style: Space/Enter reveals, 1-4 rates. The
  // handlers are skipped while typing in a field, while a dialog is open, and
  // on the buttons themselves (Space/Enter already activate those natively).
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (phase !== 'reviewing' || keyDialog !== null) return
      const target = event.target instanceof HTMLElement ? event.target : null
      const tag = target?.tagName.toLowerCase() ?? ''
      if (
        tag === 'input' ||
        tag === 'textarea' ||
        tag === 'select' ||
        target?.isContentEditable
      ) {
        return
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return

      if (event.key === 'Escape') {
        if (selectedToken) {
          event.preventDefault()
          setSelectedToken(null)
        }
        return
      }

      if (
        !revealed &&
        !sentenceLoading &&
        !generationError &&
        (event.key === ' ' || event.key === 'Enter') &&
        tag !== 'button' &&
        tag !== 'a'
      ) {
        event.preventDefault()
        void handleReveal()
        return
      }

      const rating = RATING_KEYS[event.key]
      if (rating !== undefined && revealed && !ratingPending) {
        event.preventDefault()
        void handleRate(rating)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  })

  if (phase === 'loading') {
    return (
      <div className="mx-auto grid min-h-[70vh] max-w-7xl place-items-center px-4">
        <div className="text-center" data-testid="session-loading">
          <div className="mx-auto h-10 w-10 animate-spin rounded-full border-2 border-line border-t-vermilion" />
          <p className="mt-4 font-medium text-ink-muted">오늘의 복습을 준비하고 있어요…</p>
        </div>
      </div>
    )
  }

  if (phase === 'error') {
    return (
      <div className="mx-auto grid min-h-[70vh] max-w-xl place-items-center px-4 py-12 text-center">
        <section className="surface-card w-full p-8">
          <span className="font-serif-jp text-4xl text-danger" aria-hidden="true">待</span>
          <h2 className="mt-3 text-2xl font-bold">복습을 시작할 수 없어요</h2>
          <p className="mt-2 text-ink-muted" data-testid="session-error">{fatalError}</p>
          <button type="button" onClick={() => void begin()} className="mt-6 rounded-2xl bg-ink px-5 py-3 font-semibold text-white transition hover:bg-forest focus:outline-none focus:ring-4 focus:ring-forest/20">
            다시 시도
          </button>
        </section>
      </div>
    )
  }

  if (phase === 'key-required') {
    return (
      <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 sm:py-12" data-testid="session-key-required">
        <section className="mb-6 flex items-start gap-4 rounded-3xl border border-vermilion/15 bg-vermilion-soft/60 p-5 sm:p-6">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-white font-serif-jp text-2xl text-vermilion" aria-hidden="true">鍵</span>
          <div>
            <h2 className="text-xl font-bold tracking-tight sm:text-2xl">복습을 시작하려면 Gemini API 키가 필요해요</h2>
            <p className="mt-2 text-sm leading-6 text-ink-muted">
              예문은 내 무료 Gemini API 키로 만들어요. 아래 순서대로 키를 연결하면 바로 복습이 시작돼요.
            </p>
          </div>
        </section>
        <GeminiKeySetup
          compact
          status={NOT_CONNECTED}
          onStatusChange={notifyKeyStatus}
          onConnected={() => void begin()}
          onLater={onLater}
        />
      </div>
    )
  }

  if (phase === 'no-due') {
    return (
      <div className="mx-auto grid min-h-[70vh] max-w-xl place-items-center px-4 py-12">
        <section className="surface-card w-full p-8 text-center sm:p-12" data-testid="no-due">
          <div className="mx-auto grid h-20 w-20 place-items-center rounded-full bg-forest-soft font-serif-jp text-4xl text-forest" aria-hidden="true">休</div>
          <h2 className="mt-6 text-2xl font-bold">오늘 복습은 모두 끝났어요</h2>
          <p className="mt-3 leading-7 text-ink-muted">지금 복습할 단어가 없습니다.<br />새 단어를 추가하거나 다음 복습일에 다시 만나요.</p>
          <div className="mt-7 flex flex-wrap justify-center gap-2">
            {onOpenWords && (
              <button
                type="button"
                onClick={onOpenWords}
                className="rounded-2xl border border-line bg-white px-5 py-3 text-sm font-semibold text-ink-muted transition hover:text-ink focus:outline-none focus:ring-4 focus:ring-forest/15"
              >
                단어장 보기
              </button>
            )}
            {onLater && (
              <button
                type="button"
                onClick={onLater}
                className="rounded-2xl bg-vermilion px-5 py-3 text-sm font-semibold text-white shadow-soft transition hover:bg-vermilion-deep focus:outline-none focus:ring-4 focus:ring-vermilion/20"
              >
                새 단어 추가하기
              </button>
            )}
          </div>
        </section>
      </div>
    )
  }

  if (phase === 'complete') {
    return (
      <div className="mx-auto max-w-2xl px-4 py-10 sm:px-6 sm:py-16" data-testid="summary">
        <section className="surface-card overflow-hidden text-center">
          <div className="bg-forest px-6 py-10 text-white">
            <span className="font-serif-jp text-5xl" aria-hidden="true">完</span>
            <h2 className="mt-3 text-3xl font-bold">오늘의 복습 완료</h2>
            <p className="mt-2 text-white/70">꾸준한 한 번이 오래 남는 기억을 만들어요.</p>
          </div>
          <div className="p-6 sm:p-8">
            <p className="text-sm text-ink-muted">복습한 단어</p>
            <p className="mt-1 text-5xl font-bold text-vermilion" data-testid="summary-total">{summary?.total_reviewed ?? 0}</p>
            {summary && (
              <ul className="mt-7 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {Object.entries(summary.counts_by_rating).map(([level, count]) => (
                  <li key={level} data-testid={`summary-count-${level}`} className="rounded-2xl bg-washi p-3">
                    <span className="block text-xs text-ink-faint">{SUMMARY_LABELS[level] ?? `단계 ${level}`}</span>
                    <span className="mt-1 block text-xl font-bold text-ink">{count}</span>
                  </li>
                ))}
              </ul>
            )}
            <button type="button" onClick={() => void begin()} className="mt-8 rounded-2xl bg-vermilion px-6 py-3 font-semibold text-white shadow-soft transition hover:-translate-y-0.5 hover:bg-vermilion-deep focus:outline-none focus:ring-4 focus:ring-vermilion/20">
              복습 목록 다시 확인
            </button>
          </div>
        </section>
      </div>
    )
  }

  const progressPercent = Math.max(0, Math.min(100, (progress.current / progress.total) * 100))

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-10 lg:px-8">
      <div className="mb-5 flex items-end justify-between gap-4">
        <div>
          <p className="font-serif-jp text-sm font-semibold tracking-[0.18em] text-vermilion">今日の復習</p>
          <h2 className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl">문장에서 떠올리기</h2>
        </div>
        <div className="text-right">
          <p className="text-sm font-semibold text-ink"><span className="text-vermilion">{progress.current}</span> / {progress.total}</p>
          <p className="mt-0.5 text-xs text-ink-faint">오늘의 단어</p>
        </div>
      </div>
      <div
        className="mb-6 h-1.5 overflow-hidden rounded-full bg-line/70"
        role="progressbar"
        aria-label="복습 진행률"
        aria-valuemin={0}
        aria-valuemax={progress.total}
        aria-valuenow={Math.min(progress.current, progress.total)}
      >
        <div className="h-full rounded-full bg-vermilion transition-all duration-500" style={{ width: `${progressPercent}%` }} />
      </div>

      <p className="sr-only" role="status" aria-live="polite">
        {sentenceLoading
          ? '새 예문을 만드는 중입니다.'
          : sentence
            ? '예문이 준비되었습니다. 강조된 단어의 뜻을 떠올린 뒤 정답을 확인하세요.'
            : ''}
      </p>

      <section className="surface-card min-h-[19rem] p-5 sm:p-8 lg:p-10">
        {sentenceLoading && (
          <div className="grid min-h-56 place-items-center text-center" data-testid="sentence-loading">
            <div>
              <div className="mx-auto flex w-fit gap-1.5" aria-hidden="true">
                <span className="h-2 w-2 animate-bounce rounded-full bg-vermilion [animation-delay:-0.3s]" />
                <span className="h-2 w-2 animate-bounce rounded-full bg-vermilion [animation-delay:-0.15s]" />
                <span className="h-2 w-2 animate-bounce rounded-full bg-vermilion" />
              </div>
              <p className="mt-4 font-medium text-ink-muted">새로운 예문을 만들고 있어요…</p>
              <p className="mt-1 text-xs text-ink-faint">복습할 때마다 다른 문장을 준비합니다.</p>
            </div>
          </div>
        )}

        {!sentenceLoading && generationError && (
          <div className="grid min-h-56 place-items-center text-center" data-testid="generation-error">
            <div className="max-w-lg">
              <div
                className={`mx-auto grid h-14 w-14 place-items-center rounded-full font-serif-jp text-2xl ${
                  generationError.kind === 'gemini_quota_exceeded'
                    ? 'bg-[#f5eddd] text-gold'
                    : generationError.kind === 'gemini_key_required' || generationError.kind === 'invalid_gemini_key'
                      ? 'bg-vermilion-soft text-vermilion'
                      : 'bg-danger-soft text-danger'
                }`}
                aria-hidden="true"
              >
                {generationError.kind === 'gemini_quota_exceeded'
                  ? '限'
                  : generationError.kind === 'gemini_key_required' || generationError.kind === 'invalid_gemini_key'
                    ? '鍵'
                    : '！'}
              </div>
              <h3 className="mt-4 text-xl font-bold">{generationError.title}</h3>
              <p className="mt-2 text-sm leading-6 text-ink-muted">{generationError.detail}</p>
              {generationError.kind === 'gemini_key_required' ? (
                <button
                  type="button"
                  onClick={() => setKeyDialog({ mode: 'setup' })}
                  className="mt-5 rounded-2xl bg-vermilion px-5 py-2.5 text-sm font-semibold text-white shadow-soft transition hover:bg-vermilion-deep focus:outline-none focus:ring-4 focus:ring-vermilion/20"
                  data-testid="open-key-setup"
                >
                  키 연결 가이드 열기
                </button>
              ) : generationError.kind === 'invalid_gemini_key' ? (
                <button
                  type="button"
                  onClick={() => setKeyDialog({ mode: 'replace' })}
                  className="mt-5 rounded-2xl bg-vermilion px-5 py-2.5 text-sm font-semibold text-white shadow-soft transition hover:bg-vermilion-deep focus:outline-none focus:ring-4 focus:ring-vermilion/20"
                  data-testid="replace-key"
                >
                  새 키로 바꾸기
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => sessionId && currentWord && void loadSentence(sessionId, currentWord)}
                  className="mt-5 rounded-2xl bg-ink px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-forest focus:outline-none focus:ring-4 focus:ring-forest/20"
                  data-testid="retry-generation"
                >
                  {generationError.kind === 'gemini_quota_exceeded' ? '다시 시도' : '예문 다시 만들기'}
                </button>
              )}
            </div>
          </div>
        )}

        {!sentenceLoading && !generationError && (
          <SentenceRenderer sentence={sentence} onSelectToken={setSelectedToken} />
        )}
      </section>

      <div className="mt-3">
        <LookupPanel token={selectedToken} onDismiss={() => setSelectedToken(null)} />
      </div>

      {currentWord && (
        <RatingControls
          wordForm={currentWord.word_form}
          answer={answer}
          revealed={revealed}
          selectedRating={selectedRating}
          onReveal={() => void handleReveal()}
          onRate={(rating) => void handleRate(rating)}
          errorMessage={ratingError}
        />
      )}

      <p className="mt-3 hidden text-center text-xs text-ink-faint sm:block" aria-hidden="true">
        <kbd className="rounded border border-line bg-white px-1.5 py-0.5 font-sans">Space</kbd> 정답 확인
        <span className="mx-2 text-line">|</span>
        <kbd className="rounded border border-line bg-white px-1.5 py-0.5 font-sans">1</kbd>–
        <kbd className="rounded border border-line bg-white px-1.5 py-0.5 font-sans">4</kbd> 평가
      </p>

      {keyDialog && (
        <Dialog
          labelledBy="key-dialog-title"
          onClose={() => setKeyDialog(null)}
          testId="key-dialog"
        >
          <div className="mb-5 flex items-start justify-between gap-4">
            <div>
              <p className="font-serif-jp text-sm font-semibold tracking-[0.18em] text-vermilion">鍵の設定</p>
              <h2 id="key-dialog-title" className="mt-1 text-2xl font-bold tracking-tight">
                {keyDialog.mode === 'replace' ? '새 Gemini API 키로 바꾸기' : 'Gemini API 키 연결하기'}
              </h2>
              <p className="mt-1 text-sm text-ink-muted">연결이 끝나면 지금 단어의 예문을 바로 다시 만들어요.</p>
            </div>
            <button
              type="button"
              onClick={() => setKeyDialog(null)}
              aria-label="닫기"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-ink-muted transition hover:bg-white hover:text-ink focus:outline-none focus:ring-4 focus:ring-forest/15"
            >
              ✕
            </button>
          </div>
          <GeminiKeySetup
            compact
            mode={keyDialog.mode}
            status={keyDialog.mode === 'setup' ? NOT_CONNECTED : undefined}
            onStatusChange={notifyKeyStatus}
            onConnected={handleDialogConnected}
          />
        </Dialog>
      )}
    </div>
  )
}

export default ReviewSession

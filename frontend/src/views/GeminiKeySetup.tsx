import { useEffect, useId, useRef, useState } from 'react'
import { deleteGeminiKey, getGeminiKeyStatus, putGeminiKey } from '../api/endpoints'
import {
  ApiError,
  type GeminiKeyConnectResult,
  type GeminiKeyStatus,
} from '../api/types'

export const AI_STUDIO_API_KEY_URL = 'https://aistudio.google.com/apikey'

// Format-agnostic bounds mirroring the backend rule (Req 10.6): a trimmed key
// is well-formed when it is non-empty, has no internal whitespace, contains no
// control character, and is 20–200 characters long. Used only for a
// non-blocking hint; the backend check is always the one that decides, and any
// key shape Google issues (AIza…, AQ.…, or future) is accepted.
const KEY_MIN_LENGTH = 20
const KEY_MAX_LENGTH = 200

/** True when `value` looks well-formed under the backend's format-agnostic rule. */
export function looksWellFormed(value: string): boolean {
  const trimmed = value.trim()
  if (trimmed.length < KEY_MIN_LENGTH || trimmed.length > KEY_MAX_LENGTH) return false
  // Reject any whitespace or control/non-printable character.
  // eslint-disable-next-line no-control-regex
  return !/[\s\u0000-\u001f\u007f-\u009f]/.test(trimmed)
}

type Tone = 'danger' | 'warning' | 'success'
interface Feedback {
  tone: Tone
  title: string
  detail: string
}

/** Korean copy for every `PUT /api/gemini-key` failure code (Req 10.6–10.9). */
const ERROR_FEEDBACK: Record<string, Omit<Feedback, 'tone'> & { tone?: Tone }> = {
  invalid_key_format: {
    title: '키 형식이 맞지 않아요',
    detail:
      'AI Studio에 표시된 전체 키를 그대로 복사해 붙여넣어 주세요. 키 중간에 공백이나 줄바꿈이 들어가지 않았는지, 일부가 빠지지 않았는지 확인해 주세요.',
  },
  invalid_key: {
    title: '사용할 수 없는 키예요',
    detail:
      'Google이 이 키를 거부했어요. 삭제된 키이거나 잘못 복사됐을 수 있어요. AI Studio에서 키를 다시 복사해 붙여넣어 주세요.',
  },
  key_verification_in_progress: {
    tone: 'warning',
    title: '이미 키를 확인하고 있어요',
    detail: '앞서 보낸 키의 확인이 끝나지 않았어요. 잠시 후 다시 눌러 주세요.',
  },
  could_not_verify: {
    tone: 'warning',
    title: '지금은 키를 확인할 수 없어요',
    detail:
      'Google 서버와 연결이 원활하지 않아 확인하지 못했어요. 키는 저장되지 않았으니 잠시 후 다시 시도해 주세요.',
  },
  key_storage_unavailable: {
    title: '키를 저장할 수 없는 상태예요',
    detail:
      '서버의 암호화 설정이 준비되지 않았어요. 앱을 운영하는 사람에게 TOKEN_ENCRYPTION_KEY 설정을 확인해 달라고 알려 주세요.',
  },
  key_storage_failed: {
    title: '키를 저장하지 못했어요',
    detail: '저장 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요.',
  },
}

function errorFeedback(error: unknown, hadKey: boolean): Feedback {
  const known = error instanceof ApiError ? ERROR_FEEDBACK[error.code] : undefined
  const base = known ?? {
    title: '연결하지 못했어요',
    detail: '네트워크 상태를 확인한 뒤 다시 시도해 주세요.',
  }
  const keepNote = hadKey ? ' 기존에 연결된 키는 그대로 사용돼요.' : ''
  return { tone: base.tone ?? 'danger', title: base.title, detail: base.detail + keepNote }
}

const FEEDBACK_STYLES: Record<Tone, string> = {
  danger: 'border-danger/20 bg-danger-soft text-danger',
  warning: 'border-gold/25 bg-[#f7efe0] text-[#8a6326]',
  success: 'border-forest/15 bg-forest-soft text-forest',
}

/** Inline label for an English button the Learner will see on AI Studio. */
function UiLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="whitespace-nowrap rounded-md border border-line bg-white px-1.5 py-0.5 font-mono text-[0.8em] font-semibold text-ink">
      {children}
    </span>
  )
}

function Spinner() {
  return (
    <span
      className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
      aria-hidden="true"
      data-testid="verify-spinner"
    />
  )
}

export function maskedHint(hint: string | null): string {
  return `····${hint ?? ''}`
}

export interface GeminiKeySetupProps {
  /** Known key status (e.g. from `/api/auth/me`); fetched when omitted. */
  status?: GeminiKeyStatus | null
  /** `replace` opens the key input even when a key is already connected. */
  mode?: 'setup' | 'replace'
  /** Single-column layout for use inside the Review view or a dialog. */
  compact?: boolean
  /** Called whenever the stored key status changes (connect or remove). */
  onStatusChange?: (status: GeminiKeyStatus) => void
  /** Called after a key is verified and stored. */
  onConnected?: (result: GeminiKeyConnectResult) => void
  /** "나중에": leave the guide (adding words works without a key, Req 10.2). */
  onLater?: () => void
  /** Optional call to action shown once connected (e.g. go to review). */
  onContinue?: () => void
  continueLabel?: string
}

/**
 * Key_Setup_Guide (Req 10.3–10.15): five plain-language steps to get a free
 * Gemini API key from Google AI Studio, the required notices, and a paste
 * field that verifies the key through the backend.
 *
 * The key exists only in this component's state. It is never written to
 * storage, a URL, or the console, and it is cleared after a successful connect.
 */
export function GeminiKeySetup({
  status: initialStatus,
  mode = 'setup',
  compact = false,
  onStatusChange,
  onConnected,
  onLater,
  onContinue,
  continueLabel = '복습하러 가기',
}: GeminiKeySetupProps) {
  const [status, setStatus] = useState<GeminiKeyStatus | null>(initialStatus ?? null)
  const [editing, setEditing] = useState<boolean>(
    mode === 'replace' || !initialStatus?.connected,
  )
  const [keyValue, setKeyValue] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [confirmingRemove, setConfirmingRemove] = useState(false)
  const [removing, setRemoving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const inputId = useId()
  const hintId = useId()

  // Fetch the status when the caller does not already know it.
  useEffect(() => {
    if (initialStatus) return
    let active = true
    getGeminiKeyStatus()
      .then((fetched) => {
        if (!active) return
        setStatus(fetched)
        if (fetched.connected && mode !== 'replace') setEditing(false)
      })
      .catch(() => {
        /* Status stays unknown; the form still works. */
      })
    return () => {
      active = false
    }
    // Run once on mount; later changes come from this component itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const connected = status?.connected === true
  const trimmed = keyValue.trim()
  const showFormatHint = trimmed.length > 0 && !looksWellFormed(trimmed)

  function updateStatus(next: GeminiKeyStatus) {
    setStatus(next)
    onStatusChange?.(next)
  }

  async function handleConnect(event: React.FormEvent) {
    event.preventDefault()
    if (verifying || keyValue.length === 0) return
    setVerifying(true)
    setFeedback(null)
    try {
      const result = await putGeminiKey(keyValue)
      setKeyValue('')
      setShowKey(false)
      setEditing(false)
      setConfirmingRemove(false)
      updateStatus({ connected: true, key_hint: result.key_hint })
      setFeedback(
        result.quota_exhausted_notice
          ? {
              tone: 'warning',
              title: '키는 연결됐지만, 지금은 사용량 한도에 도달했어요',
              detail:
                '무료 등급의 사용량 한도가 초기화되면(분당 한도는 약 1분 뒤, 일일 한도는 다음 날) 예문 생성이 자동으로 다시 동작해요.',
            }
          : {
              tone: 'success',
              title: '연결 완료!',
              detail: '이제 복습할 때마다 새 예문을 만들 수 있어요.',
            },
      )
      onConnected?.(result)
    } catch (error) {
      setFeedback(errorFeedback(error, connected))
      inputRef.current?.focus()
    } finally {
      setVerifying(false)
    }
  }

  async function handleRemove() {
    setRemoving(true)
    setFeedback(null)
    try {
      const next = await deleteGeminiKey()
      updateStatus(next)
      setConfirmingRemove(false)
      setEditing(true)
      setFeedback({
        tone: 'success',
        title: '키를 삭제했어요',
        detail: '새 키를 연결할 때까지 예문을 만들 수 없어요. 단어 추가는 계속할 수 있어요.',
      })
    } catch (error) {
      setFeedback(errorFeedback(error, true))
    } finally {
      setRemoving(false)
    }
  }

  function startReplace() {
    setFeedback(null)
    setConfirmingRemove(false)
    setEditing(true)
    window.setTimeout(() => inputRef.current?.focus(), 0)
  }

  function cancelReplace() {
    setKeyValue('')
    setShowKey(false)
    setFeedback(null)
    setEditing(false)
  }

  const steps: { title: string; body: React.ReactNode }[] = [
    {
      title: 'Google AI Studio 열기',
      body: (
        <>
          <p>아래 버튼을 누르면 새 탭에서 Google AI Studio의 API 키 페이지가 열려요. 이 탭은 그대로 두세요.</p>
          <a
            href={AI_STUDIO_API_KEY_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-3 inline-flex items-center gap-2 rounded-xl bg-forest px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:-translate-y-0.5 hover:bg-[#284f45] focus:outline-none focus:ring-4 focus:ring-forest/20"
            data-testid="ai-studio-link"
          >
            Google AI Studio 열기
            <span aria-hidden="true">↗</span>
            <span className="sr-only">(새 탭에서 열림)</span>
          </a>
        </>
      ),
    },
    {
      title: 'Google 계정으로 로그인',
      body: (
        <p>
          평소 쓰는 Google 계정으로 로그인하세요. 페이지가 영어로 보여도 괜찮아요. 처음이라면 이용 약관 동의 창이 나올 수 있는데, 확인 후 계속하면 돼요.
        </p>
      ),
    },
    {
      title: 'API 키 만들기',
      body: (
        <p>
          <UiLabel>Create API key</UiLabel> 버튼을 누르세요. 프로젝트를 고르라고 하면 목록에서 기존 Google Cloud 프로젝트를 선택하거나, <UiLabel>Create project</UiLabel>로 새 프로젝트를 만들면 돼요. 무엇을 골라도 괜찮아요.
        </p>
      ),
    },
    {
      title: '키 복사하기',
      body: (
        <p>
          만들어진 키 옆의 <UiLabel>Copy</UiLabel> 버튼을 눌러 복사하세요. 키는 <span className="font-mono font-semibold text-ink">AQ.</span> 또는 <span className="font-mono font-semibold text-ink">AIza</span>로 시작할 수 있어요. AI Studio에 표시된 전체 키를 그대로 복사해 붙여넣으면 돼요.
        </p>
      ),
    },
    {
      title: '붙여넣고 연결 확인',
      body: (
        <p>
          이 화면의 입력칸에 키를 붙여넣고 <span className="font-semibold text-ink">연결 확인</span>을 누르면 끝이에요.
        </p>
      ),
    },
  ]

  const guide = (
    <section aria-labelledby="key-steps-title">
      <h3 id="key-steps-title" className="text-sm font-bold tracking-[0.12em] text-ink-faint">
        무료 키 받는 방법 · 약 2분
      </h3>
      <ol className="mt-4 space-y-3" data-testid="key-steps">
        {steps.map((step, index) => (
          <li key={step.title} className="flex gap-4 rounded-2xl border border-line/80 bg-white/70 p-4">
            <span
              className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-vermilion text-sm font-bold text-white"
              aria-hidden="true"
            >
              {index + 1}
            </span>
            <div className="min-w-0 text-sm leading-6 text-ink-muted">
              <p className="font-semibold text-ink">
                <span className="sr-only">{index + 1}단계: </span>
                {step.title}
              </p>
              <div className="mt-1">{step.body}</div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )

  const notices = (
    <section aria-labelledby="key-notices-title" className="rounded-2xl border border-line/80 bg-washi/70 p-4 sm:p-5">
      <h3 id="key-notices-title" className="text-sm font-bold text-ink">알아 두세요</h3>
      <ul className="mt-3 space-y-2 text-sm leading-6 text-ink-muted" data-testid="key-notices">
        <li className="flex gap-2"><span aria-hidden="true" className="text-forest">●</span>키 만들기는 <strong className="font-semibold text-ink">무료</strong>이고 결제 카드가 필요 없어요.</li>
        <li className="flex gap-2"><span aria-hidden="true" className="text-forest">●</span>무료 등급은 사용량 제한(분당·일일 요청 수)이 있어요. 한도에 닿으면 잠시 기다렸다가 다시 시도하면 돼요.</li>
        <li className="flex gap-2"><span aria-hidden="true" className="text-forest">●</span>Gemini API 약관에 따라 <strong className="font-semibold text-ink">만 18세 이상</strong>만 사용할 수 있어요.</li>
        <li className="flex gap-2"><span aria-hidden="true" className="text-forest">●</span>유럽경제지역(EEA)·스위스·영국에 거주한다면 결제가 연결된 <strong className="font-semibold text-ink">유료 키</strong>를 사용해야 해요.</li>
      </ul>
      <div className="mt-4 flex gap-2.5 rounded-xl bg-white/80 p-3 text-xs leading-5 text-ink-muted" data-testid="key-safety">
        <svg viewBox="0 0 24 24" className="mt-0.5 h-4 w-4 shrink-0 text-forest" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
          <rect x="5" y="10" width="14" height="10" rx="2" />
          <path d="M8 10V7a4 4 0 0 1 8 0v3" />
        </svg>
        <p>
          키는 암호화해서 저장하고, 화면에는 마지막 4자리만 보여 드려요. 언제든 삭제할 수 있어요. 키는 비밀번호와 같으니 다른 사람에게 알려 주지 마세요.
        </p>
      </div>
    </section>
  )

  const connectedPanel = connected && (
    <div className="rounded-2xl border border-forest/15 bg-forest-soft/70 p-4" data-testid="key-connected">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="flex items-center gap-2 font-semibold text-forest">
          <span className="h-2.5 w-2.5 rounded-full bg-forest" aria-hidden="true" />
          연결됨 <span className="font-mono tracking-wider" data-testid="key-hint">{maskedHint(status?.key_hint ?? null)}</span>
        </p>
        {!editing && !confirmingRemove && (
          <div className="flex gap-2">
            <button
              type="button"
              onClick={startReplace}
              className="rounded-xl border border-forest/20 bg-white px-3 py-1.5 text-sm font-semibold text-forest transition hover:bg-forest hover:text-white focus:outline-none focus:ring-4 focus:ring-forest/15"
            >
              다른 키로 바꾸기
            </button>
            <button
              type="button"
              onClick={() => {
                setFeedback(null)
                setConfirmingRemove(true)
              }}
              className="rounded-xl px-3 py-1.5 text-sm font-semibold text-danger transition hover:bg-danger-soft focus:outline-none focus:ring-4 focus:ring-danger/15"
            >
              키 삭제
            </button>
          </div>
        )}
      </div>
      {confirmingRemove && (
        <div className="mt-3 rounded-xl border border-danger/20 bg-white p-3" role="alertdialog" aria-labelledby="remove-title" data-testid="remove-confirm">
          <p id="remove-title" className="text-sm font-semibold text-ink">이 키를 삭제할까요?</p>
          <p className="mt-1 text-sm leading-6 text-ink-muted">삭제하면 새 키를 연결할 때까지 예문을 만들 수 없어요.</p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => void handleRemove()}
              disabled={removing}
              className="rounded-xl bg-danger px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-[#933f35] focus:outline-none focus:ring-4 focus:ring-danger/20 disabled:opacity-60"
            >
              {removing ? '삭제하는 중…' : '삭제'}
            </button>
            <button
              type="button"
              onClick={() => setConfirmingRemove(false)}
              disabled={removing}
              className="rounded-xl px-3 py-1.5 text-sm font-semibold text-ink-muted transition hover:bg-washi focus:outline-none focus:ring-4 focus:ring-forest/15"
            >
              취소
            </button>
          </div>
        </div>
      )}
      {editing && (
        <p className="mt-2 text-xs leading-5 text-forest/80">새 키가 저장될 때까지 지금 키를 계속 사용해요.</p>
      )}
    </div>
  )

  const form = editing && (
    <form onSubmit={(event) => void handleConnect(event)} noValidate data-testid="key-form">
      <label htmlFor={inputId} className="font-semibold text-ink">
        {connected ? '새 Gemini API 키' : 'Gemini API 키'}
      </label>
      <div className="mt-2 flex gap-2">
        <div className="relative min-w-0 flex-1">
          <input
            ref={inputRef}
            id={inputId}
            name="gemini-api-key"
            type={showKey ? 'text' : 'password'}
            value={keyValue}
            onChange={(event) => setKeyValue(event.target.value)}
            disabled={verifying}
            autoComplete="off"
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            placeholder="AI Studio에서 복사한 전체 키를 붙여넣으세요"
            aria-describedby={hintId}
            className="focus-ring w-full rounded-2xl border border-line bg-white py-3 pl-4 pr-20 font-mono text-sm text-ink shadow-sm transition placeholder:font-sans placeholder:text-ink-faint/70 hover:border-ink-faint disabled:cursor-wait disabled:bg-washi"
          />
          <button
            type="button"
            onClick={() => setShowKey((value) => !value)}
            disabled={verifying}
            aria-label={showKey ? '키 숨기기' : '키 보기'}
            aria-pressed={showKey}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-ink-muted transition hover:bg-washi hover:text-ink focus:outline-none focus:ring-2 focus:ring-forest/20"
          >
            {showKey ? '숨기기' : '보기'}
          </button>
        </div>
      </div>
      <p id={hintId} className={`mt-2 text-xs leading-5 ${showFormatHint ? 'text-[#8a6326]' : 'text-ink-faint'}`} data-testid="format-hint">
        {showFormatHint
          ? `키가 조금 짧거나 공백이 섞인 것 같아요 (지금 ${trimmed.length}자). 그래도 연결 확인을 눌러 볼 수 있어요.`
          : 'AQ. 또는 AIza로 시작하는 키를 붙여넣으세요. 앞뒤 공백은 자동으로 지워져요.'}
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="submit"
          disabled={verifying || keyValue.length === 0}
          aria-busy={verifying}
          className="inline-flex items-center justify-center gap-2 rounded-2xl bg-vermilion px-5 py-3 font-semibold text-white shadow-soft transition hover:-translate-y-0.5 hover:bg-[#c75f43] focus:outline-none focus:ring-4 focus:ring-vermilion/20 disabled:translate-y-0 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {verifying && <Spinner />}
          {verifying ? '확인하는 중…' : '연결 확인'}
        </button>
        {connected && (
          <button
            type="button"
            onClick={cancelReplace}
            disabled={verifying}
            className="rounded-2xl px-4 py-3 text-sm font-semibold text-ink-muted transition hover:bg-washi focus:outline-none focus:ring-4 focus:ring-forest/15"
          >
            취소
          </button>
        )}
      </div>
    </form>
  )

  const feedbackBox = feedback && (
    <div
      className={`rounded-2xl border p-4 ${FEEDBACK_STYLES[feedback.tone]}`}
      role={feedback.tone === 'success' ? 'status' : 'alert'}
      data-testid="key-feedback"
    >
      <p className="font-semibold">{feedback.title}</p>
      <p className="mt-1 text-sm leading-6 opacity-90">{feedback.detail}</p>
    </div>
  )

  const actions = (onLater || (connected && onContinue)) && (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {onLater && !connected && (
        <button
          type="button"
          onClick={onLater}
          className="rounded-2xl px-4 py-2.5 text-sm font-semibold text-ink-muted transition hover:bg-white hover:text-ink focus:outline-none focus:ring-4 focus:ring-forest/15"
        >
          나중에 할게요 · 단어 먼저 추가하기
        </button>
      )}
      {connected && onContinue && (
        <button
          type="button"
          onClick={onContinue}
          className="rounded-2xl bg-ink px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-forest focus:outline-none focus:ring-4 focus:ring-forest/20"
        >
          {continueLabel} →
        </button>
      )}
    </div>
  )

  const keyCard = (
    <section className="surface-card space-y-5 p-5 sm:p-7" aria-label="Gemini API 키 연결">
      <div>
        <span className="font-serif-jp text-2xl text-vermilion" aria-hidden="true">鍵</span>
        <h3 className="mt-1 text-xl font-bold tracking-tight">
          {connected ? '내 Gemini API 키' : '키 붙여넣기'}
        </h3>
        {!connected && (
          <p className="mt-1 text-sm leading-6 text-ink-muted">복사한 키를 붙여넣으면 바로 사용할 수 있는지 확인해 드려요.</p>
        )}
      </div>
      {connectedPanel}
      {form}
      {feedbackBox}
    </section>
  )

  if (compact) {
    return (
      <div className="space-y-5" data-testid="gemini-key-setup">
        {guide}
        {keyCard}
        {notices}
        {actions}
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-12 lg:px-8" data-testid="gemini-key-setup">
      <header className="max-w-2xl">
        <span className="font-serif-jp text-sm font-semibold tracking-[0.2em] text-vermilion">鍵の設定</span>
        <h2 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">Gemini API 키 연결하기</h2>
        <p className="mt-4 text-base leading-7 text-ink-muted">
          예문은 내 Google 계정의 무료 Gemini API 키로 만들어요. 아래 5단계를 따라 하면 2분 정도면 끝나요. 단어 추가는 키 없이도 할 수 있어요.
        </p>
      </header>
      <div className="mt-8 grid gap-6 lg:grid-cols-[1.1fr_0.9fr] lg:gap-10">
        {guide}
        <div className="space-y-5">
          {keyCard}
          {notices}
          {actions}
        </div>
      </div>
    </div>
  )
}

export default GeminiKeySetup

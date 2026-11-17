import { useCallback, useEffect, useId, useRef, useState } from 'react'
import {
  cancelImportPreview,
  confirmImport,
  createImportPreview,
  getImportPreview,
} from '../api/endpoints'
import {
  ApiError,
  type ApiErrorCode,
  type FieldMapping,
  type ImportPreview,
  type ImportSummary,
  type SkipReason,
} from '../api/types'
import {
  MAPPING_ROLES,
  validateMappingClient,
  type MappingErrorReason,
  type MappingRole,
} from './importMapping'

// Same limit the server enforces (Req 2.3). The client check is UX only.
export const MAX_UPLOAD_BYTES = 52_428_800
export const ACCEPTED_EXTENSIONS = '.apkg,.txt,.tsv'
const SKIPPED_LIST_LIMIT = 20

type Phase = 'idle' | 'uploading' | 'preview' | 'confirming' | 'summary'
type Tone = 'danger' | 'warning'

interface Notice {
  tone?: Tone
  title: string
  detail?: string
}

/** Korean copy for every import error code (Req 2.2, 2.3, 2.11, 2.31, 2.36, 2.41, 2.42). */
export const IMPORT_ERROR_NOTICES: Record<string, Notice> = {
  unsupported_colpkg: {
    title: 'Anki 컬렉션 패키지(.colpkg)는 지원하지 않아요',
    detail:
      'Anki에서 덱을 .apkg 덱 패키지(Anki Deck Package)나 텍스트 노트(Notes in Plain Text, .txt)로 내보낸 뒤 다시 올려 주세요.',
  },
  file_too_large: {
    title: '파일이 너무 커요',
    detail: '50 MB(52,428,800바이트) 이하의 파일만 올릴 수 있어요. 덱을 나눠서 내보내 보세요.',
  },
  unsupported_extension: {
    title: '지원하지 않는 파일 형식이에요',
    detail: '.apkg, .txt, .tsv 파일만 올릴 수 있어요.',
  },
  unreadable_file: {
    title: '파일을 읽을 수 없어요',
    detail: '파일이 손상되었거나 내용이 확장자와 맞지 않아요. Anki에서 덱을 다시 내보내 주세요.',
  },
  unsafe_package: {
    title: '안전하지 않은 패키지라서 열지 않았어요',
    detail:
      '패키지 안에 허용되지 않는 경로가 있거나 덱 데이터가 200 MB를 넘어요. Anki에서 덱을 다시 내보내 주세요.',
  },
  invalid_text_export: {
    title: '텍스트 파일 형식이 맞지 않아요',
    detail: 'UTF-8로 저장된, 탭으로 구분된 Anki 텍스트 내보내기 파일만 지원해요.',
  },
  unrecognized_format: {
    title: 'Anki 덱 파일이 아니에요',
    detail: '파일 안에서 Anki 덱 데이터를 찾지 못했어요. Anki에서 내보낸 파일인지 확인해 주세요.',
  },
  no_notes: {
    title: '덱에 노트가 없어요',
    detail: '가져올 노트가 하나도 없어요. 노트가 들어 있는 덱을 내보냈는지 확인해 주세요.',
  },
  invalid_note_type: {
    tone: 'warning',
    title: '노트 유형을 찾을 수 없어요',
    detail: '목록에 있는 노트 유형을 다시 골라 주세요.',
  },
  invalid_mapping: {
    tone: 'warning',
    title: '필드 선택을 확인해 주세요',
    detail: '표시된 항목의 필드를 다시 골라 주세요.',
  },
  preview_expired: {
    title: '미리보기가 만료되었어요. 파일을 다시 올려 주세요.',
    detail: '미리보기는 30분 동안만 유지되고, 한 번 가져오거나 취소하면 다시 쓸 수 없어요.',
  },
  import_in_progress: {
    tone: 'warning',
    title: '다른 가져오기가 진행 중이에요. 끝난 뒤 다시 시도해 주세요.',
  },
  import_failed: {
    title: '가져오기에 실패해 단어를 하나도 추가하지 않았어요.',
    detail: '단어장은 가져오기 전과 같아요. 잠시 후 파일을 다시 올려 주세요.',
  },
}

const NETWORK_NOTICE: Notice = {
  title: '서버에 연결하지 못했어요',
  detail: '네트워크 상태를 확인한 뒤 다시 시도해 주세요.',
}
const GENERIC_NOTICE: Notice = {
  title: '문제가 생겼어요',
  detail: '잠시 후 다시 시도해 주세요.',
}

function noticeFor(error: unknown): Notice {
  if (error instanceof ApiError) {
    const known = IMPORT_ERROR_NOTICES[error.code]
    if (known) return known
    if (error.status === 0) return NETWORK_NOTICE
  }
  return GENERIC_NOTICE
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function hasCode(error: unknown, ...codes: ApiErrorCode[]): boolean {
  return error instanceof ApiError && codes.includes(error.code)
}

/** Lowercased suffix from the last `.` of the basename, or "". */
function extensionOf(filename: string): string {
  const base = filename.split(/[\\/]/).pop() ?? ''
  const dot = base.lastIndexOf('.')
  return dot >= 0 ? base.slice(dot).toLowerCase() : ''
}

/** UX-only prechecks (Req 2.2, 2.3); the server's ordered checks stay authoritative. */
export function precheckFile(file: File): Notice | null {
  if (extensionOf(file.name) === '.colpkg') return IMPORT_ERROR_NOTICES.unsupported_colpkg
  if (file.size > MAX_UPLOAD_BYTES) return IMPORT_ERROR_NOTICES.file_too_large
  return null
}

const ROLE_LABELS: Record<MappingRole, string> = { word: '단어', reading: '읽기', meaning: '뜻' }
const ROLE_SELECT_LABELS: Record<MappingRole, string> = {
  word: '단어 (필수)',
  reading: '읽기 (선택)',
  meaning: '뜻 (필수)',
}

/** Per-role mapping error copy (Req 2.22). */
export function mappingErrorMessage(role: MappingRole, reason: MappingErrorReason): string {
  if (reason === 'duplicate') return '다른 항목과 같은 필드예요'
  if (reason === 'out_of_range') return `${ROLE_LABELS[role]} 필드를 다시 선택하세요`
  return `${ROLE_LABELS[role]} 필드를 선택하세요`
}

/** Korean labels for the summary's skip reasons (Req 2.27–2.29, 2.34). */
export const SKIP_REASON_LABELS: Record<SkipReason, string> = {
  missing_field: '빈 항목',
  duplicate: '중복',
  invalid_length: '길이 초과',
}

function isRole(value: string): value is MappingRole {
  return (MAPPING_ROLES as readonly string[]).includes(value)
}

/**
 * Turn a server `invalid_mapping` response into Korean per-role messages.
 * The reason comes from the client rules when they agree, else from the
 * server message, so the Learner never sees the English server text.
 */
function serverMappingErrors(
  error: ApiError,
  mapping: FieldMapping,
  fieldCount: number,
): Partial<Record<MappingRole, string>> {
  const clientReasons = validateMappingClient(mapping, fieldCount)
  const result: Partial<Record<MappingRole, string>> = {}
  for (const { field, message } of error.fieldErrors) {
    if (!isRole(field)) continue
    const reason: MappingErrorReason =
      clientReasons[field] ??
      (/same|duplicate|share/i.test(message)
        ? 'duplicate'
        : field === 'reading' || /range/i.test(message)
          ? 'out_of_range'
          : 'missing')
    result[field] = mappingErrorMessage(field, reason)
  }
  return result
}

function formatCount(value: number): string {
  return value.toLocaleString('ko-KR')
}

const NOTICE_STYLES: Record<Tone, string> = {
  danger: 'border-danger/20 bg-danger-soft text-danger',
  warning: 'border-gold/25 bg-[#f7efe0] text-[#8a6326]',
}

function NoticeBox({ notice, testId }: { notice: Notice; testId: string }) {
  return (
    <div
      role="alert"
      data-testid={testId}
      className={`rounded-2xl border px-4 py-3 text-sm ${NOTICE_STYLES[notice.tone ?? 'danger']}`}
    >
      <p className="font-semibold">{notice.title}</p>
      {notice.detail && <p className="mt-1 leading-6 opacity-90">{notice.detail}</p>}
    </div>
  )
}

/** Inline label for an English menu name the Learner will see in Anki. */
function UiLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="whitespace-nowrap rounded-md border border-line bg-white px-1.5 py-0.5 font-mono text-[0.8em] font-semibold text-ink">
      {children}
    </span>
  )
}

function ExportHelp() {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  return (
    <div className="mt-5 rounded-2xl border border-line/80 bg-white/70">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
        className="focus-ring flex w-full items-center justify-between gap-3 rounded-2xl px-4 py-3 text-left text-sm font-semibold text-ink"
      >
        <span>
          <span className="mr-2 font-serif-jp text-vermilion" aria-hidden="true">?</span>
          Anki에서 내보내는 방법
        </span>
        <span aria-hidden="true" className={`text-ink-faint transition ${open ? 'rotate-180' : ''}`}>⌄</span>
      </button>
      {open && (
        <div id={panelId} data-testid="export-help" className="border-t border-line/70 px-4 pb-4 pt-3 text-sm leading-6 text-ink-muted">
          <ol className="list-decimal space-y-1.5 pl-5">
            <li>컴퓨터에서 Anki 데스크톱 앱을 열고, 가져올 덱을 선택해요.</li>
            <li>
              메뉴에서 <UiLabel>파일(File)</UiLabel> → <UiLabel>내보내기(Export…)</UiLabel>를 눌러요.
            </li>
            <li>
              형식(Export format)을 <UiLabel>Anki Deck Package (*.apkg)</UiLabel>로 골라요. 또는{' '}
              <UiLabel>Notes in Plain Text (*.txt)</UiLabel>도 괜찮아요.
            </li>
            <li>내보낼 덱이 맞는지 확인하고 <UiLabel>내보내기(Export…)</UiLabel> 버튼을 눌러 파일을 저장해요.</li>
            <li>저장한 파일을 이 화면에 끌어다 놓거나 “파일 선택”으로 골라요.</li>
          </ol>
          <ul className="mt-3 space-y-1 rounded-xl bg-washi/80 px-3 py-2 text-xs leading-5">
            <li>• 단어·읽기·뜻만 가져와요. Anki의 스케줄 정보(복습 기록)와 미디어(소리·그림)는 가져오지 않아요. 그래서 내보낼 때 스케줄·미디어 포함 옵션은 꺼도 돼요.</li>
            <li>• 컬렉션 전체 패키지(.colpkg)는 지원하지 않아요. 덱 하나씩 .apkg 또는 .txt로 내보내 주세요.</li>
            <li>• Anki 메뉴 이름은 언어 설정에 따라 다르게 보일 수 있어서 영어 이름을 함께 적었어요.</li>
          </ul>
        </div>
      )}
    </div>
  )
}

export interface ImportDeckProps {
  /** "복습하러 가기" on the summary card. */
  onGoToReview?: () => void
}

const SELECT_CLASS =
  'focus-ring mt-2 w-full rounded-2xl border bg-white px-4 py-3 text-base text-ink shadow-sm transition'
const PRIMARY_BUTTON =
  'flex items-center justify-center gap-2 rounded-2xl bg-vermilion px-5 py-3.5 font-semibold text-white shadow-soft transition hover:-translate-y-0.5 hover:bg-[#c75f43] focus:outline-none focus:ring-4 focus:ring-vermilion/20 disabled:cursor-wait disabled:translate-y-0 disabled:opacity-60'
const QUIET_BUTTON =
  'rounded-2xl px-5 py-3.5 font-semibold text-ink-muted transition hover:bg-white hover:text-ink focus:outline-none focus:ring-4 focus:ring-forest/15 disabled:opacity-60'

/**
 * Anki deck import panel (Req 2): idle → uploading → preview → confirming →
 * summary. Nothing is imported until the Learner confirms the preview.
 */
export function ImportDeck({ onGoToReview }: ImportDeckProps) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [idleNotice, setIdleNotice] = useState<Notice | null>(null)
  const [fileName, setFileName] = useState('')
  const [progress, setProgress] = useState(0)
  const [dragging, setDragging] = useState(false)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [mapping, setMapping] = useState<FieldMapping>({ word: null, reading: null, meaning: null })
  const [serverErrors, setServerErrors] = useState<Partial<Record<MappingRole, string>>>({})
  const [previewNotice, setPreviewNotice] = useState<Notice | null>(null)
  const [switchingType, setSwitchingType] = useState(false)
  const [summary, setSummary] = useState<ImportSummary | null>(null)

  const inputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const pendingIdRef = useRef<string | null>(null)
  const mountedRef = useRef(true)
  const switchTokenRef = useRef(0)
  const idPrefix = useId()

  // Leaving the view: abort an upload and send a best-effort cancel for a
  // pending preview. The 30-minute expiry covers anything this misses.
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      abortRef.current?.abort()
      abortRef.current = null
      const pending = pendingIdRef.current
      pendingIdRef.current = null
      if (pending) void cancelImportPreview(pending).catch(() => {})
    }
  }, [])

  const showPreview = useCallback((next: ImportPreview) => {
    setPreview(next)
    setMapping({ ...next.suggested_mapping })
    setServerErrors({})
  }, [])

  const backToIdle = useCallback((notice: Notice | null) => {
    pendingIdRef.current = null
    abortRef.current = null
    switchTokenRef.current += 1
    setPreview(null)
    setSummary(null)
    setServerErrors({})
    setPreviewNotice(null)
    setSwitchingType(false)
    setProgress(0)
    setIdleNotice(notice)
    setPhase('idle')
  }, [])

  async function startUpload(file: File) {
    const blocked = precheckFile(file)
    setFileName(file.name)
    if (blocked) {
      setIdleNotice(blocked)
      return
    }
    const controller = new AbortController()
    abortRef.current = controller
    setIdleNotice(null)
    setProgress(0)
    setPhase('uploading')
    try {
      const created = await createImportPreview(
        file,
        (fraction) => {
          if (abortRef.current === controller) setProgress(fraction)
        },
        controller.signal,
      )
      if (!mountedRef.current || abortRef.current !== controller) {
        // Superseded or unmounted while the server answered: drop the preview.
        void cancelImportPreview(created.preview_id).catch(() => {})
        return
      }
      abortRef.current = null
      pendingIdRef.current = created.preview_id
      showPreview(created)
      setPreviewNotice(null)
      setPhase('preview')
    } catch (error) {
      if (!mountedRef.current || abortRef.current !== controller) return
      abortRef.current = null
      if (isAbort(error)) {
        backToIdle(null)
        return
      }
      if (hasCode(error, 'unauthorized')) return // the shell shows sign-in
      backToIdle(noticeFor(error))
    }
  }

  function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return
    if (files.length > 1) {
      setIdleNotice({ title: '파일은 한 번에 하나만 올릴 수 있어요', detail: '가져올 덱 파일 하나를 골라 주세요.' })
      return
    }
    void startUpload(files[0])
  }

  function cancelUpload() {
    abortRef.current?.abort()
  }

  async function switchNoteType(noteTypeId: string) {
    if (!preview || noteTypeId === preview.selected_note_type_id) return
    const token = ++switchTokenRef.current
    setSwitchingType(true)
    setPreviewNotice(null)
    try {
      const next = await getImportPreview(preview.preview_id, noteTypeId)
      if (!mountedRef.current || token !== switchTokenRef.current) return
      showPreview(next)
    } catch (error) {
      if (!mountedRef.current || token !== switchTokenRef.current) return
      if (hasCode(error, 'unauthorized')) return
      if (hasCode(error, 'preview_expired')) {
        backToIdle(IMPORT_ERROR_NOTICES.preview_expired)
        return
      }
      setPreviewNotice(noticeFor(error))
    } finally {
      if (mountedRef.current && token === switchTokenRef.current) setSwitchingType(false)
    }
  }

  function chooseField(role: MappingRole, raw: string) {
    const index = raw === '' ? null : Number(raw)
    setMapping((current) => ({ ...current, [role]: index }))
    setServerErrors((current) => {
      if (!current[role]) return current
      const next = { ...current }
      delete next[role]
      return next
    })
  }

  async function handleConfirm() {
    if (!preview || phase !== 'preview') return
    setServerErrors({})
    setPreviewNotice(null)
    // A client-invalid mapping is not sent; its errors are already shown inline.
    if (Object.keys(validateMappingClient(mapping, preview.fields.length)).length > 0) return

    const sent = { ...mapping }
    const body =
      preview.file_kind === 'apkg' && preview.selected_note_type_id !== null
        ? { note_type_id: preview.selected_note_type_id, mapping: sent }
        : { mapping: sent }
    setPhase('confirming')
    try {
      const result = await confirmImport(preview.preview_id, body)
      if (!mountedRef.current) return
      pendingIdRef.current = null
      setSummary(result)
      setPreview(null)
      setPhase('summary')
    } catch (error) {
      if (!mountedRef.current) return
      if (hasCode(error, 'unauthorized')) return
      if (hasCode(error, 'preview_expired', 'import_failed')) {
        backToIdle(noticeFor(error))
        return
      }
      if (error instanceof ApiError && error.code === 'invalid_mapping') {
        setServerErrors(serverMappingErrors(error, sent, preview.fields.length))
      }
      setPreviewNotice(noticeFor(error))
      setPhase('preview')
    }
  }

  function handleCancelPreview() {
    const pending = pendingIdRef.current
    if (pending) void cancelImportPreview(pending).catch(() => {})
    backToIdle(null)
  }

  // --- Idle / uploading -------------------------------------------------------

  if (phase === 'idle' || phase === 'uploading') {
    const percent = Math.round(progress * 100)
    const reading = phase === 'uploading' && percent >= 100
    return (
      <div data-testid="import-deck">
        {phase === 'idle' ? (
          <div
            data-testid="import-drop-zone"
            onDragOver={(event) => {
              event.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault()
              setDragging(false)
              handleFiles(event.dataTransfer.files)
            }}
            className={`flex flex-col items-center rounded-3xl border-2 border-dashed px-5 py-10 text-center transition ${
              dragging ? 'border-forest bg-forest-soft/60' : 'border-line bg-washi/60'
            }`}
          >
            <span className="font-serif-jp text-4xl text-ink-faint" aria-hidden="true">札</span>
            <p className="mt-3 font-semibold text-ink">Anki 덱 파일을 여기에 끌어다 놓으세요</p>
            <p className="mt-1 text-sm text-ink-muted">또는</p>
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className={`${PRIMARY_BUTTON} mt-3 px-6 py-3`}
            >
              파일 선택
            </button>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPTED_EXTENSIONS}
              className="hidden"
              aria-label="Anki 덱 파일"
              data-testid="import-file-input"
              onChange={(event) => {
                handleFiles(event.target.files)
                event.target.value = ''
              }}
            />
            <p className="mt-4 text-xs text-ink-faint">.apkg · .txt · .tsv 파일, 최대 50 MB</p>
          </div>
        ) : (
          <div data-testid="import-uploading" className="rounded-3xl border border-line bg-washi/60 px-5 py-8">
            <div className="flex items-center justify-between gap-3 text-sm">
              <p className="min-w-0 truncate font-semibold text-ink" title={fileName}>{fileName}</p>
              <span className="shrink-0 tabular-nums text-ink-muted">{percent}%</span>
            </div>
            <div
              role="progressbar"
              aria-label="업로드 진행률"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
              className="mt-3 h-2.5 overflow-hidden rounded-full bg-line/70"
            >
              <div className="h-full rounded-full bg-forest transition-[width]" style={{ width: `${percent}%` }} />
            </div>
            <div className="mt-4 flex items-center justify-between gap-3">
              <p role="status" className="text-sm text-ink-muted">
                {reading ? '덱을 읽는 중…' : '파일을 올리는 중…'}
              </p>
              <button type="button" onClick={cancelUpload} className={`${QUIET_BUTTON} px-4 py-2 text-sm`}>
                업로드 취소
              </button>
            </div>
          </div>
        )}

        {phase === 'idle' && idleNotice && (
          <div className="mt-5">
            <NoticeBox notice={idleNotice} testId="import-error" />
          </div>
        )}

        <ExportHelp />
      </div>
    )
  }

  // --- Summary ----------------------------------------------------------------

  if (phase === 'summary' && summary) {
    const totalSkipped =
      summary.skipped_missing_field + summary.skipped_duplicate + summary.skipped_invalid_length
    const chips: [string, number][] = [
      [SKIP_REASON_LABELS.missing_field, summary.skipped_missing_field],
      [SKIP_REASON_LABELS.duplicate, summary.skipped_duplicate],
      [SKIP_REASON_LABELS.invalid_length, summary.skipped_invalid_length],
    ]
    return (
      <div data-testid="import-summary">
        <div role="status" className="rounded-3xl border border-forest/15 bg-forest-soft px-5 py-5 text-forest">
          <p className="font-serif-jp text-sm tracking-[0.2em]">完了</p>
          <p className="mt-1 text-xl font-bold" data-testid="import-created">
            ✓ 단어 {formatCount(summary.created)}개를 추가했어요
          </p>
          {summary.created > 0 && <p className="mt-1 text-sm text-ink-muted">추가한 단어는 오늘 복습에 바로 나타나요.</p>}
        </div>

        <ul className="mt-4 flex flex-wrap gap-2" aria-label="건너뛴 노트 수">
          {chips.map(([label, count]) => (
            <li key={label} className="rounded-full border border-line bg-white px-3 py-1.5 text-sm font-semibold text-ink-muted">
              {label} <span className="tabular-nums text-ink">{formatCount(count)}</span>
            </li>
          ))}
        </ul>

        {summary.skipped.length > 0 && (
          <div className="mt-5">
            <p className="text-sm font-semibold text-ink">건너뛴 노트</p>
            <ul className="mt-2 divide-y divide-line/70 rounded-2xl border border-line/80 bg-white" data-testid="import-skipped">
              {summary.skipped.slice(0, SKIPPED_LIST_LIMIT).map((note, index) => (
                <li key={index} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                  <span
                    className={`min-w-0 truncate ${note.word_form === null ? 'text-ink-faint' : 'font-serif-jp text-ink'}`}
                    title={note.word_form ?? undefined}
                  >
                    {note.word_form ?? '(빈 단어)'}
                  </span>
                  <span className="shrink-0 rounded-full bg-vermilion-soft px-2.5 py-0.5 text-xs font-semibold text-vermilion">
                    {SKIP_REASON_LABELS[note.reason] ?? note.reason}
                  </span>
                </li>
              ))}
            </ul>
            {totalSkipped > SKIPPED_LIST_LIMIT && (
              <p className="mt-2 text-xs text-ink-faint">건너뛴 노트 중 처음 20개만 보여요</p>
            )}
          </div>
        )}

        <div className="mt-7 flex flex-col gap-2 sm:flex-row sm:justify-end">
          <button type="button" onClick={() => backToIdle(null)} className={QUIET_BUTTON}>
            다른 덱 가져오기
          </button>
          {onGoToReview && (
            <button type="button" onClick={onGoToReview} className={PRIMARY_BUTTON}>
              복습하러 가기
            </button>
          )}
        </div>
      </div>
    )
  }

  // --- Preview / confirming ---------------------------------------------------

  if (!preview) return null
  const confirming = phase === 'confirming'
  const clientErrors = validateMappingClient(mapping, preview.fields.length)
  const roleOf = (fieldIndex: number) => MAPPING_ROLES.filter((role) => mapping[role] === fieldIndex)

  return (
    <div data-testid="import-preview">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="min-w-0 truncate font-semibold text-ink" title={fileName}>{fileName}</p>
        <p className="shrink-0 text-sm font-semibold text-forest" data-testid="import-note-count">
          노트 {formatCount(preview.note_count)}개
        </p>
      </div>

      {preview.file_kind === 'apkg' && preview.note_types.length > 0 && (
        <div className="mt-5">
          <label htmlFor={`${idPrefix}-note-type`} className="font-semibold text-ink">노트 유형</label>
          <select
            id={`${idPrefix}-note-type`}
            value={preview.selected_note_type_id ?? ''}
            disabled={confirming || switchingType}
            onChange={(event) => void switchNoteType(event.target.value)}
            className={`${SELECT_CLASS} border-line hover:border-ink-faint disabled:opacity-60`}
          >
            {preview.note_types.map((type) => (
              <option key={type.id} value={type.id}>
                {type.name} ({formatCount(type.note_count)}개)
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="mt-5 grid gap-4 sm:grid-cols-3">
        {MAPPING_ROLES.map((role) => {
          const selectId = `${idPrefix}-${role}`
          const clientReason = clientErrors[role]
          const message = clientReason ? mappingErrorMessage(role, clientReason) : serverErrors[role]
          const value = mapping[role]
          return (
            <div key={role}>
              <label htmlFor={selectId} className="font-semibold text-ink">{ROLE_SELECT_LABELS[role]}</label>
              <select
                id={selectId}
                value={value === null ? '' : String(value)}
                disabled={confirming || switchingType}
                onChange={(event) => chooseField(role, event.target.value)}
                aria-invalid={message ? true : undefined}
                aria-describedby={message ? `${selectId}-error` : undefined}
                className={`${SELECT_CLASS} disabled:opacity-60 ${message ? 'border-danger bg-danger-soft/30' : 'border-line hover:border-ink-faint'}`}
              >
                {role === 'reading' ? (
                  <option value="">없음</option>
                ) : (
                  <option value="" disabled>필드를 선택하세요</option>
                )}
                {preview.fields.map((field) => (
                  <option key={field.index} value={String(field.index)}>{field.name}</option>
                ))}
              </select>
              {message && (
                <p id={`${selectId}-error`} data-testid={`mapping-error-${role}`} className="mt-2 flex items-center gap-1.5 text-sm text-danger">
                  <span aria-hidden="true">●</span> {message}
                </p>
              )}
            </div>
          )
        })}
      </div>

      <div className="mt-6">
        <p className="text-sm font-semibold text-ink">
          미리보기 <span className="font-normal text-ink-faint">(처음 {Math.min(5, preview.samples.length)}개 노트)</span>
        </p>
        <div className={`mt-2 overflow-x-auto rounded-2xl border border-line/80 bg-white ${switchingType ? 'opacity-60' : ''}`}>
          <table className="w-full min-w-max border-collapse text-left text-sm" data-testid="import-samples">
            <thead>
              <tr className="border-b border-line/80 bg-washi/70">
                {preview.fields.map((field) => (
                  <th key={field.index} scope="col" className="px-3 py-2 align-bottom font-semibold text-ink">
                    <span className="flex flex-wrap items-center gap-1.5">
                      {field.name}
                      {roleOf(field.index).map((role) => (
                        <span
                          key={role}
                          data-testid={`role-badge-${role}`}
                          className="rounded-full bg-vermilion-soft px-2 py-0.5 text-[11px] font-semibold text-vermilion"
                        >
                          {ROLE_LABELS[role]}
                        </span>
                      ))}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.samples.slice(0, 5).map((row, rowIndex) => (
                <tr key={rowIndex} className="border-b border-line/50 last:border-0">
                  {preview.fields.map((field, column) => {
                    const cell = row[column] ?? ''
                    return (
                      <td key={field.index} className="max-w-[16rem] px-3 py-2 align-top text-ink-muted">
                        <span className="line-clamp-2 break-words" title={cell}>{cell}</span>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-ink-faint">
          가져오기 전에는 단어장이 바뀌지 않아요. 미리보기는 30분 동안 유지돼요.
        </p>
      </div>

      {previewNotice && (
        <div className="mt-5">
          <NoticeBox notice={previewNotice} testId="import-preview-error" />
        </div>
      )}

      <div className="mt-7 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={handleCancelPreview} disabled={confirming} className={QUIET_BUTTON}>
          취소
        </button>
        <button
          type="button"
          onClick={() => void handleConfirm()}
          disabled={confirming || switchingType}
          className={PRIMARY_BUTTON}
        >
          {confirming ? '가져오는 중…' : `${formatCount(preview.note_count)}개 노트 가져오기`}
        </button>
      </div>
    </div>
  )
}

export default ImportDeck

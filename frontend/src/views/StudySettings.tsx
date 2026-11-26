import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { getStudySettings, putStudySettings } from '../api/endpoints'
import { ApiError, type StudySettings as StudySettingsValues } from '../api/types'

export const STUDY_LIMIT_MAX = 1000
export const STUDY_LIMIT_ERROR = '0부터 1000 사이의 정수를 입력하세요'

/**
 * True when `text` is a whole number from 0 to 1000 written only with digits
 * (Req 11.5). "", "-1", "1.5", "1e2", and "abc" are all rejected.
 */
export function validateStudyLimit(text: string): boolean {
  return /^\d+$/.test(text) && Number(text) <= STUDY_LIMIT_MAX
}

type FieldName = keyof StudySettingsValues

const FIELDS: { name: FieldName; label: string; helper: string }[] = [
  { name: 'new_word_limit', label: '신규 단어', helper: '처음 학습하는 단어, 기본 50' },
  { name: 'review_word_limit', label: '복습 단어', helper: '복습 날짜가 된 단어, 기본 100' },
]

type Values = Record<FieldName, string>

function toValues(settings: StudySettingsValues): Values {
  return {
    new_word_limit: String(settings.new_word_limit),
    review_word_limit: String(settings.review_word_limit),
  }
}

function isFieldName(field: string): field is FieldName {
  return field === 'new_word_limit' || field === 'review_word_limit'
}

/**
 * StudySettings view (Req 11.3–11.5): edit how many New_Words and how many
 * Review_Words one Review_Session includes. Values are held as raw strings so
 * an empty or partial entry is never silently coerced.
 */
export function StudySettings() {
  const [values, setValues] = useState<Values | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveFailed, setSaveFailed] = useState(false)
  const [serverErrors, setServerErrors] = useState<Partial<Record<FieldName, true>>>({})
  const mounted = useRef(true)
  const idPrefix = useId()

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const load = useCallback(() => {
    setLoadFailed(false)
    setValues(null)
    getStudySettings()
      .then((settings) => {
        if (mounted.current) setValues(toValues(settings))
      })
      .catch(() => {
        // A 401 is handled by the shell's onSignedOut.
        if (mounted.current) setLoadFailed(true)
      })
  }, [])

  useEffect(() => {
    load()
  }, [load])

  function update(name: FieldName, value: string) {
    setValues((current) => (current ? { ...current, [name]: value } : current))
    setSaved(false)
    setSaveFailed(false)
    setServerErrors((current) => {
      if (!current[name]) return current
      const next = { ...current }
      delete next[name]
      return next
    })
  }

  const allValid =
    values !== null &&
    validateStudyLimit(values.new_word_limit) &&
    validateStudyLimit(values.review_word_limit)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!values || !allValid || saving) return
    setSaving(true)
    setSaved(false)
    setSaveFailed(false)
    setServerErrors({})
    try {
      const stored = await putStudySettings({
        new_word_limit: Number(values.new_word_limit),
        review_word_limit: Number(values.review_word_limit),
      })
      if (!mounted.current) return
      setValues(toValues(stored))
      setSaved(true)
    } catch (error) {
      if (!mounted.current) return
      if (error instanceof ApiError && error.code === 'invalid_study_settings') {
        const next: Partial<Record<FieldName, true>> = {}
        for (const fieldError of error.fieldErrors) {
          if (isFieldName(fieldError.field)) next[fieldError.field] = true
        }
        if (Object.keys(next).length > 0) setServerErrors(next)
        else setSaveFailed(true)
      } else {
        setSaveFailed(true)
      }
    } finally {
      if (mounted.current) setSaving(false)
    }
  }

  let body: React.ReactNode
  if (loadFailed) {
    body = (
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-danger/15 bg-danger-soft px-4 py-3" role="alert" data-testid="settings-load-error">
        <p className="text-sm font-medium text-danger">설정을 불러오지 못했어요.</p>
        <button
          type="button"
          onClick={load}
          className="rounded-xl border border-danger/20 bg-white px-3 py-1.5 text-sm font-semibold text-danger transition hover:bg-danger hover:text-white focus:outline-none focus:ring-4 focus:ring-danger/15"
        >
          다시 시도
        </button>
      </div>
    )
  } else if (values === null) {
    body = (
      <div className="grid min-h-[12rem] place-items-center" data-testid="settings-loading">
        <div className="h-10 w-10 animate-spin rounded-full border-2 border-line border-t-vermilion" aria-label="불러오는 중" />
      </div>
    )
  } else {
    body = (
      <form onSubmit={(event) => void handleSubmit(event)} noValidate className="mt-7" data-testid="settings-form">
        <div className="grid gap-6 sm:grid-cols-2">
          {FIELDS.map(({ name, label, helper }) => {
            const inputId = `${idPrefix}-${name}`
            const helperId = `${inputId}-helper`
            const errorId = `${inputId}-error`
            const invalid = !validateStudyLimit(values[name]) || Boolean(serverErrors[name])
            return (
              <div key={name}>
                <label htmlFor={inputId} className="font-semibold text-ink">{label}</label>
                <input
                  id={inputId}
                  name={name}
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={STUDY_LIMIT_MAX}
                  step={1}
                  value={values[name]}
                  onChange={(event) => update(name, event.target.value)}
                  aria-invalid={invalid || undefined}
                  aria-describedby={invalid ? `${errorId} ${helperId}` : helperId}
                  data-testid={`input-${name}`}
                  className={`focus-ring mt-2 w-full rounded-2xl border bg-white px-4 py-3 text-base tabular-nums text-ink shadow-sm transition ${invalid ? 'border-danger bg-danger-soft/30' : 'border-line hover:border-ink-faint'}`}
                />
                <p id={helperId} className="mt-2 text-xs text-ink-faint">{helper}</p>
                {invalid && (
                  <p id={errorId} data-testid={`error-${name}`} className="mt-1.5 flex items-center gap-1.5 text-sm text-danger">
                    <span aria-hidden="true">●</span> {STUDY_LIMIT_ERROR}
                  </p>
                )}
              </div>
            )
          })}
        </div>

        {saveFailed && (
          <p role="alert" data-testid="settings-save-error" className="mt-6 rounded-2xl border border-danger/15 bg-danger-soft px-4 py-3 text-sm font-medium text-danger">
            저장하지 못했어요. 잠시 후 다시 시도해 주세요.
          </p>
        )}
        {saved && (
          <p role="status" data-testid="settings-saved" className="mt-6 rounded-2xl border border-forest/15 bg-forest-soft px-4 py-3 text-sm font-medium text-forest">
            ✓ 저장했어요. 다음 복습 세션부터 적용돼요.
          </p>
        )}

        <div className="mt-7 flex justify-end">
          <button
            type="submit"
            disabled={!allValid || saving}
            aria-busy={saving}
            className="inline-flex items-center justify-center gap-2 rounded-2xl bg-vermilion px-6 py-3 font-semibold text-white shadow-soft transition hover:-translate-y-0.5 hover:bg-vermilion-deep focus:outline-none focus:ring-4 focus:ring-vermilion/20 disabled:translate-y-0 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving && (
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden="true" />
            )}
            {saving ? '저장하는 중…' : '저장'}
          </button>
        </div>
      </form>
    )
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-8 sm:px-6 sm:py-12 lg:px-8" data-testid="study-settings">
      <section className="surface-card p-5 sm:p-8" aria-labelledby={`${idPrefix}-title`}>
        <span className="font-serif-jp text-2xl text-vermilion" aria-hidden="true">設</span>
        <h2 id={`${idPrefix}-title`} className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl">학습 설정</h2>
        <p className="mt-2 text-sm leading-6 text-ink-muted">
          한 번의 복습 세션에 포함할 단어 수를 정해요. 저장한 값은 다음 세션부터 적용돼요.
        </p>
        {body}
      </section>
    </div>
  )
}

export default StudySettings

import { useId, useState } from 'react'
import { addWord } from '../api/endpoints'
import { ApiError, type WordEntryOut } from '../api/types'
import ImportDeck from './ImportDeck'

type FieldName = 'word_form' | 'reading' | 'meaning'

const FIELDS: {
  name: FieldName
  label: string
  englishLabel: string
  max: number
  placeholder: string
  helper: string
}[] = [
  { name: 'word_form', label: '일본어 단어', englishLabel: 'Word', max: 50, placeholder: '例）懐かしい', helper: '한자, 히라가나 또는 가타카나' },
  { name: 'reading', label: '읽기', englishLabel: 'Reading', max: 100, placeholder: 'なつかしい', helper: '히라가나로 적어 주세요' },
  { name: 'meaning', label: '뜻', englishLabel: 'Meaning', max: 500, placeholder: '그리운, 그립다', helper: '문맥에서 떠올릴 핵심 뜻' },
]

type EntryTab = 'manual' | 'import'

const TABS: { id: EntryTab; label: string }[] = [
  { id: 'manual', label: '직접 입력' },
  { id: 'import', label: 'Anki 덱 가져오기' },
]

const INTRO: Record<EntryTab, { eyebrow: string; title: string; body: string; quote: string; quoteKo: string }> = {
  manual: {
    eyebrow: '新しい言葉',
    title: '새 단어를 담아 보세요',
    body: '외우고 싶은 단어의 읽기와 뜻을 함께 적어 주세요. 추가한 단어는 오늘 복습에 바로 나타납니다.',
    quote: '言葉は、文脈の中で生きる。',
    quoteKo: '단어는 문맥 속에서 살아납니다.',
  },
  import: {
    eyebrow: '単語帳の引っ越し',
    title: 'Anki 덱을 한 번에 가져오세요',
    body: 'Anki에서 내보낸 .apkg 또는 텍스트 파일을 올리면 미리보기에서 단어·읽기·뜻 필드를 고른 뒤 가져올 수 있어요. 가져온 단어는 오늘 복습에 바로 나타납니다.',
    quote: '積み重ねた言葉を、ここへ。',
    quoteKo: '쌓아 온 단어를 이곳으로.',
  },
}

export interface AddWordProps {
  onAdded?: (word: WordEntryOut) => void
  /** "복습하러 가기" on the import summary. */
  onGoToReview?: () => void
}

/** Manual word entry with backend field validation and exact deduplication. */
export function AddWord({ onAdded, onGoToReview }: AddWordProps) {
  const [tab, setTab] = useState<EntryTab>('manual')
  const tabsId = useId()
  const [values, setValues] = useState<Record<FieldName, string>>({ word_form: '', reading: '', meaning: '' })
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<FieldName, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function update(name: FieldName, value: string) {
    setValues((current) => ({ ...current, [name]: value }))
    if (fieldErrors[name]) {
      setFieldErrors((current) => ({ ...current, [name]: undefined }))
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setFieldErrors({})
    setFormError(null)
    setSuccess(null)
    try {
      const word = await addWord(values)
      setSuccess(`「${word.word_form}」을(를) 단어장에 추가했어요.`)
      setValues({ word_form: '', reading: '', meaning: '' })
      onAdded?.(word)
    } catch (err) {
      if (err instanceof ApiError && err.kind === 'validation') {
        const next: Partial<Record<FieldName, string>> = {}
        for (const fieldError of err.fieldErrors) {
          next[fieldError.field as FieldName] = fieldError.message
        }
        setFieldErrors(next)
      } else if (err instanceof ApiError && err.kind === 'duplicate') {
        setFormError('이미 단어장에 있는 단어예요.')
      } else {
        setFormError(err instanceof ApiError ? err.message : '단어를 추가하지 못했어요. 다시 시도해 주세요.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[0.75fr_1.25fr] lg:gap-16">
      <section className="pt-2" data-testid="add-word-intro">
        <span className="font-serif-jp text-sm font-semibold tracking-[0.2em] text-vermilion">{INTRO[tab].eyebrow}</span>
        <h2 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">{INTRO[tab].title}</h2>
        <p className="mt-4 max-w-md text-base leading-7 text-ink-muted">{INTRO[tab].body}</p>
        <div className="mt-8 hidden rounded-3xl border border-forest/10 bg-forest-soft/70 p-5 lg:block">
          <p className="font-serif-jp text-2xl font-semibold text-forest">{INTRO[tab].quote}</p>
          <p className="mt-2 text-sm text-ink-muted">{INTRO[tab].quoteKo}</p>
        </div>
      </section>

      <div className="surface-card p-5 sm:p-8">
        <div role="tablist" aria-label="단어 추가 방법" className="mb-7 grid grid-cols-2 gap-1 rounded-2xl border border-line/80 bg-washi/70 p-1">
          {TABS.map((item) => {
            const selected = tab === item.id
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                id={`${tabsId}-tab-${item.id}`}
                aria-selected={selected}
                aria-controls={`${tabsId}-panel-${item.id}`}
                tabIndex={selected ? 0 : -1}
                onClick={() => setTab(item.id)}
                onKeyDown={(event) => {
                  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                    event.preventDefault()
                    const next: EntryTab = item.id === 'manual' ? 'import' : 'manual'
                    setTab(next)
                    document.getElementById(`${tabsId}-tab-${next}`)?.focus()
                  }
                }}
                className={
                  'rounded-xl px-3 py-2.5 text-sm font-semibold transition focus:outline-none focus:ring-2 focus:ring-forest/30 ' +
                  (selected ? 'bg-forest text-white shadow-sm' : 'text-ink-muted hover:bg-forest-soft hover:text-forest')
                }
              >
                {item.label}
              </button>
            )
          })}
        </div>

        <div
          role="tabpanel"
          id={`${tabsId}-panel-import`}
          aria-labelledby={`${tabsId}-tab-import`}
          hidden={tab !== 'import'}
        >
          <ImportDeck onGoToReview={onGoToReview} />
        </div>

        <form
          onSubmit={handleSubmit}
          noValidate
          role="tabpanel"
          id={`${tabsId}-panel-manual`}
          aria-labelledby={`${tabsId}-tab-manual`}
          hidden={tab !== 'manual'}
        >
          <div className="space-y-6">
            {FIELDS.map(({ name, label, englishLabel, max, placeholder, helper }) => {
              const hasError = Boolean(fieldErrors[name])
              const sharedProps = {
                id: name,
                name,
                value: values[name],
                maxLength: max,
                placeholder,
                onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => update(name, event.target.value),
                'aria-label': englishLabel,
                'aria-invalid': hasError || undefined,
                'aria-describedby': hasError ? `${name}-error` : `${name}-helper`,
                className: `focus-ring mt-2 w-full rounded-2xl border bg-white px-4 py-3 text-base text-ink shadow-sm transition placeholder:text-ink-faint/65 ${hasError ? 'border-danger bg-danger-soft/30' : 'border-line hover:border-ink-faint'}`,
              }
              return (
                <div key={name}>
                  <div className="flex items-end justify-between gap-3">
                    <label htmlFor={name} className="font-semibold text-ink">{label}</label>
                    <span className="text-xs tabular-nums text-ink-faint">{values[name].length} / {max}</span>
                  </div>
                  {name === 'meaning' ? <textarea {...sharedProps} rows={3} /> : <input {...sharedProps} />}
                  {fieldErrors[name] ? (
                    <p id={`${name}-error`} role="alert" data-testid={`error-${name}`} className="mt-2 flex items-center gap-1.5 text-sm text-danger">
                      <span aria-hidden="true">●</span> {fieldErrors[name]}
                    </p>
                  ) : (
                    <p id={`${name}-helper`} className="mt-2 text-xs text-ink-faint">{helper}</p>
                  )}
                </div>
              )
            })}
          </div>

          {formError && (
            <p role="alert" data-testid="form-error" className="mt-6 rounded-2xl border border-danger/15 bg-danger-soft px-4 py-3 text-sm font-medium text-danger">
              {formError}
            </p>
          )}
          {success && (
            <p role="status" data-testid="form-success" className="mt-6 rounded-2xl border border-forest/15 bg-forest-soft px-4 py-3 text-sm font-medium text-forest">
              ✓ {success}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting}
            aria-label="Add word"
            className="mt-7 flex w-full items-center justify-center gap-2 rounded-2xl bg-vermilion px-5 py-3.5 font-semibold text-white shadow-soft transition hover:-translate-y-0.5 hover:bg-[#c75f43] focus:outline-none focus:ring-4 focus:ring-vermilion/20 disabled:cursor-wait disabled:translate-y-0 disabled:opacity-60"
          >
            <span aria-hidden="true">＋</span>
            {submitting ? '추가하는 중…' : '단어장에 추가'}
          </button>
        </form>
      </div>
    </div>
  )
}

export default AddWord

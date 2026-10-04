import { useState } from 'react'
import { addWord } from '../api/endpoints'
import { ApiError, type WordEntryOut } from '../api/types'

type FieldName = 'word_form' | 'reading' | 'meaning'

const FIELDS: { name: FieldName; label: string; max: number }[] = [
  { name: 'word_form', label: 'Word', max: 50 },
  { name: 'reading', label: 'Reading', max: 100 },
  { name: 'meaning', label: 'Meaning', max: 500 },
]

export interface AddWordProps {
  /** Called after a word is successfully added. */
  onAdded?: (word: WordEntryOut) => void
}

/**
 * Add Word form (Req 1).
 *
 * Posts to `POST /api/words`. Per-field validation errors returned by the
 * backend are surfaced inline next to each named field (Req 1.2, 1.3); a
 * duplicate word form shows a form-level duplicate error (Req 1.4).
 */
export function AddWord({ onAdded }: AddWordProps) {
  const [values, setValues] = useState<Record<FieldName, string>>({
    word_form: '',
    reading: '',
    meaning: '',
  })
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<FieldName, string>>>(
    {},
  )
  const [formError, setFormError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function update(name: FieldName, value: string) {
    setValues((v) => ({ ...v, [name]: value }))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setFieldErrors({})
    setFormError(null)
    setSuccess(null)
    try {
      const word = await addWord(values)
      setSuccess(`Added "${word.word_form}".`)
      setValues({ word_form: '', reading: '', meaning: '' })
      onAdded?.(word)
    } catch (err) {
      if (err instanceof ApiError && err.kind === 'validation') {
        const next: Partial<Record<FieldName, string>> = {}
        for (const fe of err.fieldErrors) {
          next[fe.field as FieldName] = fe.message
        }
        setFieldErrors(next)
      } else if (err instanceof ApiError && err.kind === 'duplicate') {
        setFormError('That word is already in your collection.')
      } else {
        setFormError(
          err instanceof ApiError ? err.message : 'Something went wrong.',
        )
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-md space-y-4" noValidate>
      <h2 className="text-xl font-semibold">Add a word</h2>

      {FIELDS.map(({ name, label, max }) => (
        <div key={name}>
          <label htmlFor={name} className="block text-sm font-medium">
            {label}
          </label>
          <input
            id={name}
            name={name}
            value={values[name]}
            maxLength={max}
            onChange={(e) => update(name, e.target.value)}
            aria-invalid={fieldErrors[name] ? true : undefined}
            aria-describedby={fieldErrors[name] ? `${name}-error` : undefined}
            className={
              'mt-1 w-full rounded border px-3 py-2 ' +
              (fieldErrors[name] ? 'border-rose-500' : 'border-slate-300')
            }
          />
          {fieldErrors[name] && (
            <p
              id={`${name}-error`}
              role="alert"
              data-testid={`error-${name}`}
              className="mt-1 text-sm text-rose-600"
            >
              {fieldErrors[name]}
            </p>
          )}
        </div>
      ))}

      {formError && (
        <p role="alert" data-testid="form-error" className="text-sm text-rose-600">
          {formError}
        </p>
      )}
      {success && (
        <p data-testid="form-success" className="text-sm text-emerald-600">
          {success}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="rounded bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
      >
        {submitting ? 'Adding…' : 'Add word'}
      </button>
    </form>
  )
}

export default AddWord

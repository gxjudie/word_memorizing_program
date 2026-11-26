import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { deleteWord, listWords, updateWord } from '../api/endpoints'
import { ApiError, type WordEntryOut } from '../api/types'
import Dialog from '../components/Dialog'
import { WORD_FIELDS, type WordFieldName, type WordValues } from '../wordFields'

export interface WordListProps {
  /** "단어 추가" in the empty state and header. */
  onAdd?: () => void
}

const FIELDS = WORD_FIELDS

function normalize(value: string): string {
  return value.normalize('NFKC').toLowerCase()
}

/** Client-side guard mirroring the backend length rules (`word.py`). */
function validateDraft(draft: WordValues): Partial<Record<WordFieldName, string>> {
  const errors: Partial<Record<WordFieldName, string>> = {}
  for (const field of FIELDS) {
    const value = draft[field.name].trim()
    if (value === '') errors[field.name] = '필수 항목이에요.'
    else if (value.length > field.max) errors[field.name] = `${field.max}자 이내로 입력하세요.`
  }
  return errors
}

function todayIso(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

function formatDue(date: string, today: string): string {
  if (date <= today) return '오늘 복습'
  const [, month, day] = date.split('-')
  return `${Number(month)}월 ${Number(day)}일 복습`
}

/**
 * The Word_Collection screen: search, edit, and delete.
 *
 * Before this screen existed the app could add and import words but never show,
 * correct, or remove one, so a typo (or a whole mistaken deck) was permanent.
 * Search runs on the loaded collection: a personal word book is small enough
 * that an instant local filter beats a round trip per keystroke.
 */
export function WordList({ onAdd }: WordListProps = {}) {
  const [words, setWords] = useState<WordEntryOut[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<WordEntryOut | null>(null)
  const [deleting, setDeleting] = useState<WordEntryOut | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const searchId = useId()
  const today = todayIso()

  const load = useCallback(() => {
    setLoadFailed(false)
    setWords(null)
    listWords()
      .then((loaded) => setWords(loaded))
      .catch(() => setLoadFailed(true))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const visible = useMemo(() => {
    if (words === null) return []
    const needle = normalize(query.trim())
    const matches = needle
      ? words.filter((word) =>
          [word.word_form, word.reading, word.meaning].some((value) =>
            normalize(value).includes(needle),
          ),
        )
      : words
    // Newest first: the words just added or imported are the ones being checked.
    return [...matches].sort((left, right) =>
      right.created_at.localeCompare(left.created_at),
    )
  }, [words, query])

  function handleSaved(updated: WordEntryOut) {
    setWords((current) =>
      current ? current.map((word) => (word.id === updated.id ? updated : word)) : current,
    )
    setEditing(null)
    setNotice(`「${updated.word_form}」을(를) 고쳤어요.`)
  }

  function handleDeleted(removed: WordEntryOut) {
    setWords((current) => (current ? current.filter((word) => word.id !== removed.id) : current))
    setDeleting(null)
    setNotice(`「${removed.word_form}」을(를) 삭제했어요.`)
  }

  const total = words?.length ?? 0

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-12 lg:px-8" data-testid="word-list">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="font-serif-jp text-sm font-semibold tracking-[0.18em] text-vermilion">単語帳</p>
          <h2 className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl">내 단어장</h2>
          <p className="mt-2 text-sm text-ink-muted" data-testid="word-count">
            {words === null ? '불러오는 중…' : `단어 ${total}개`}
          </p>
        </div>
        <button
          type="button"
          onClick={onAdd}
          className="rounded-2xl bg-ink px-5 py-3 text-sm font-semibold text-white shadow-soft transition hover:-translate-y-0.5 hover:bg-forest focus:outline-none focus:ring-4 focus:ring-forest/20"
        >
          ＋ 단어 추가
        </button>
      </div>

      <div className="mt-6">
        <label htmlFor={searchId} className="sr-only">
          단어 검색
        </label>
        <div className="relative">
          <span
            className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-faint"
            aria-hidden="true"
          >
            ⌕
          </span>
          <input
            id={searchId}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="단어, 읽기, 뜻으로 찾기"
            className="focus-ring w-full rounded-2xl border border-line bg-white py-3 pl-11 pr-4 text-base text-ink shadow-sm placeholder:text-ink-faint"
          />
        </div>
      </div>

      {notice && (
        <p
          role="status"
          data-testid="word-list-notice"
          className="mt-4 rounded-2xl border border-forest/15 bg-forest-soft px-4 py-3 text-sm font-medium text-forest"
        >
          ✓ {notice}
        </p>
      )}

      {loadFailed && (
        <div
          role="alert"
          className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-danger/15 bg-danger-soft px-4 py-3"
        >
          <p className="text-sm font-medium text-danger">단어장을 불러오지 못했어요.</p>
          <button
            type="button"
            onClick={load}
            className="rounded-xl border border-danger/20 bg-white px-3 py-1.5 text-sm font-semibold text-danger transition hover:bg-danger hover:text-white focus:outline-none focus:ring-4 focus:ring-danger/15"
          >
            다시 시도
          </button>
        </div>
      )}

      {words === null && !loadFailed && (
        <div className="grid min-h-[16rem] place-items-center" data-testid="word-list-loading">
          <div
            className="h-10 w-10 animate-spin rounded-full border-2 border-line border-t-vermilion"
            role="status"
            aria-label="단어장 불러오는 중"
          />
        </div>
      )}

      {words !== null && total === 0 && (
        <div className="surface-card mt-6 p-8 text-center sm:p-12" data-testid="word-list-empty">
          <span className="font-serif-jp text-4xl text-ink-faint" aria-hidden="true">空</span>
          <h3 className="mt-3 text-xl font-bold">아직 담긴 단어가 없어요</h3>
          <p className="mt-2 text-sm leading-6 text-ink-muted">
            단어를 직접 추가하거나 Anki 덱을 가져와 보세요.
          </p>
          <button
            type="button"
            onClick={onAdd}
            className="mt-6 rounded-2xl bg-vermilion px-5 py-3 text-sm font-semibold text-white shadow-soft transition hover:bg-vermilion-deep focus:outline-none focus:ring-4 focus:ring-vermilion/20"
          >
            단어 추가하기
          </button>
        </div>
      )}

      {words !== null && total > 0 && visible.length === 0 && (
        <p className="mt-10 text-center text-sm text-ink-muted" data-testid="word-list-no-results">
          ‘{query.trim()}’와 맞는 단어가 없어요.
        </p>
      )}

      {visible.length > 0 && (
        <ul className="mt-6 space-y-2" aria-label="단어 목록">
          {visible.map((word) => {
            const due = word.next_review_date <= today
            return (
              <li
                key={word.id}
                data-testid={`word-row-${word.word_form}`}
                className="flex flex-wrap items-center gap-x-5 gap-y-3 rounded-2xl border border-line/80 bg-paper px-4 py-4 shadow-sm transition hover:border-forest/25 sm:px-5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="font-serif-jp text-xl font-semibold text-ink" lang="ja">
                      {word.word_form}
                    </span>
                    <span className="text-sm text-ink-muted" lang="ja">
                      {word.reading}
                    </span>
                  </div>
                  <p className="mt-1 break-words text-sm leading-6 text-ink-muted">{word.meaning}</p>
                </div>

                <span
                  className={
                    'shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ' +
                    (due ? 'bg-vermilion-soft text-vermilion' : 'bg-forest-soft text-forest')
                  }
                >
                  {formatDue(word.next_review_date, today)}
                </span>

                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setEditing(word)}
                    aria-label={`${word.word_form} 수정`}
                    className="rounded-xl px-3 py-2 text-sm font-semibold text-ink-muted transition hover:bg-forest-soft hover:text-forest focus:outline-none focus:ring-4 focus:ring-forest/15"
                  >
                    수정
                  </button>
                  <button
                    type="button"
                    onClick={() => setDeleting(word)}
                    aria-label={`${word.word_form} 삭제`}
                    className="rounded-xl px-3 py-2 text-sm font-semibold text-ink-muted transition hover:bg-danger-soft hover:text-danger focus:outline-none focus:ring-4 focus:ring-danger/15"
                  >
                    삭제
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {editing && (
        <WordEditor
          word={editing}
          onClose={() => setEditing(null)}
          onSaved={handleSaved}
        />
      )}

      {deleting && (
        <DeleteConfirm
          word={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={handleDeleted}
        />
      )}
    </div>
  )
}

interface WordEditorProps {
  word: WordEntryOut
  onClose: () => void
  onSaved: (word: WordEntryOut) => void
}

function WordEditor({ word, onClose, onSaved }: WordEditorProps) {
  const titleId = useId()
  const [draft, setDraft] = useState<WordValues>({
    word_form: word.word_form,
    reading: word.reading,
    meaning: word.meaning,
  })
  const [errors, setErrors] = useState<Partial<Record<WordFieldName, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  function update(name: WordFieldName, value: string) {
    setDraft((current) => ({ ...current, [name]: value }))
    setErrors((current) => (current[name] ? { ...current, [name]: undefined } : current))
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (saving) return
    const found = validateDraft(draft)
    if (Object.keys(found).length > 0) {
      setErrors(found)
      return
    }
    setSaving(true)
    setFormError(null)
    try {
      const saved = await updateWord(word.id, {
        word_form: draft.word_form.trim(),
        reading: draft.reading.trim(),
        meaning: draft.meaning.trim(),
      })
      onSaved(saved)
    } catch (error) {
      if (error instanceof ApiError && error.code === 'validation') {
        const next: Partial<Record<WordFieldName, string>> = {}
        for (const fieldError of error.fieldErrors) {
          next[fieldError.field as WordFieldName] = fieldError.message
        }
        setErrors(next)
      } else if (error instanceof ApiError && error.code === 'duplicate') {
        setFormError('같은 단어가 이미 단어장에 있어요.')
      } else if (error instanceof ApiError && error.code === 'not_found') {
        setFormError('이 단어를 찾을 수 없어요. 목록을 새로 불러와 주세요.')
      } else {
        setFormError('저장하지 못했어요. 잠시 후 다시 시도해 주세요.')
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog labelledBy={titleId} onClose={onClose} testId="word-editor">
      <form onSubmit={(event) => void handleSubmit(event)} noValidate data-testid="word-editor-form">
        <h2 id={titleId} className="text-2xl font-bold tracking-tight">
          단어 수정
        </h2>
        <p className="mt-1 text-sm text-ink-muted">복습 일정은 그대로 두고 표기만 고칩니다.</p>

        <div className="mt-6 space-y-4">
          {FIELDS.map((field) => {
            const error = errors[field.name]
            const inputId = `edit-${field.name}`
            return (
              <div key={field.name}>
                <div className="flex items-end justify-between gap-3">
                  <label htmlFor={inputId} className="text-sm font-semibold text-ink">
                    {field.label}
                  </label>
                  <span className="text-xs tabular-nums text-ink-faint">
                    {draft[field.name].length} / {field.max}
                  </span>
                </div>
                {field.name === 'meaning' ? (
                  <textarea
                    id={inputId}
                    name={field.name}
                    rows={3}
                    value={draft[field.name]}
                    maxLength={field.max}
                    onChange={(event) => update(field.name, event.target.value)}
                    aria-invalid={Boolean(error) || undefined}
                    aria-describedby={error ? `${inputId}-error` : undefined}
                    className={`focus-ring mt-1.5 w-full rounded-2xl border bg-white px-4 py-2.5 text-base text-ink shadow-sm ${error ? 'border-danger bg-danger-soft/30' : 'border-line'}`}
                  />
                ) : (
                  <input
                    id={inputId}
                    name={field.name}
                    value={draft[field.name]}
                    maxLength={field.max}
                    onChange={(event) => update(field.name, event.target.value)}
                    aria-invalid={Boolean(error) || undefined}
                    aria-describedby={error ? `${inputId}-error` : undefined}
                    className={`focus-ring mt-1.5 w-full rounded-2xl border bg-white px-4 py-2.5 text-base text-ink shadow-sm ${error ? 'border-danger bg-danger-soft/30' : 'border-line'}`}
                  />
                )}
                {error && (
                  <p id={`${inputId}-error`} role="alert" className="mt-1.5 text-sm text-danger">
                    {error}
                  </p>
                )}
              </div>
            )
          })}
        </div>

        {formError && (
          <p
            role="alert"
            data-testid="word-editor-error"
            className="mt-4 rounded-2xl border border-danger/15 bg-danger-soft px-4 py-3 text-sm font-medium text-danger"
          >
            {formError}
          </p>
        )}

        <div className="mt-7 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-2xl px-5 py-3 text-sm font-semibold text-ink-muted transition hover:bg-white hover:text-ink focus:outline-none focus:ring-4 focus:ring-forest/15"
          >
            취소
          </button>
          <button
            type="submit"
            disabled={saving}
            aria-busy={saving}
            className="rounded-2xl bg-vermilion px-5 py-3 text-sm font-semibold text-white shadow-soft transition hover:bg-vermilion-deep focus:outline-none focus:ring-4 focus:ring-vermilion/20 disabled:cursor-wait disabled:opacity-60"
          >
            {saving ? '저장하는 중…' : '저장'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

interface DeleteConfirmProps {
  word: WordEntryOut
  onClose: () => void
  onDeleted: (word: WordEntryOut) => void
}

function DeleteConfirm({ word, onClose, onDeleted }: DeleteConfirmProps) {
  const titleId = useId()
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleDelete() {
    if (deleting) return
    setDeleting(true)
    setError(null)
    try {
      await deleteWord(word.id)
      onDeleted(word)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'not_found') {
        // Already gone: the intent is satisfied, so drop it from the list.
        onDeleted(word)
        return
      }
      setError('삭제하지 못했어요. 잠시 후 다시 시도해 주세요.')
      setDeleting(false)
    }
  }

  return (
    <Dialog labelledBy={titleId} onClose={onClose} className="max-w-md" testId="word-delete-dialog">
      <h2 id={titleId} className="text-xl font-bold">
        이 단어를 삭제할까요?
      </h2>
      <p className="mt-2 text-sm leading-6 text-ink-muted">
        「{word.word_form}」과(와) 지금까지의 복습 기록이 함께 지워집니다. 되돌릴 수 없어요.
      </p>
      {error && (
        <p
          role="alert"
          className="mt-4 rounded-2xl border border-danger/15 bg-danger-soft px-4 py-3 text-sm font-medium text-danger"
        >
          {error}
        </p>
      )}
      <div className="mt-6 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded-2xl px-5 py-3 text-sm font-semibold text-ink-muted transition hover:bg-white hover:text-ink focus:outline-none focus:ring-4 focus:ring-forest/15"
        >
          취소
        </button>
        <button
          type="button"
          onClick={() => void handleDelete()}
          disabled={deleting}
          aria-busy={deleting}
          data-testid="confirm-delete"
          className="rounded-2xl bg-danger px-5 py-3 text-sm font-semibold text-white shadow-soft transition hover:bg-[#8f3a30] focus:outline-none focus:ring-4 focus:ring-danger/20 disabled:cursor-wait disabled:opacity-60"
        >
          {deleting ? '삭제하는 중…' : '삭제'}
        </button>
      </div>
    </Dialog>
  )
}

export default WordList

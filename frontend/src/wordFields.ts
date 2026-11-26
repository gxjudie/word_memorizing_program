/**
 * The three editable word fields, shared by the add form and the word-list
 * editor so labels, limits, and helper copy cannot drift apart.
 *
 * The maximum lengths mirror the backend schema (`backend/app/schemas/word.py`);
 * the server remains the authority and returns field-exact errors.
 */
export type WordFieldName = 'word_form' | 'reading' | 'meaning'

export interface WordFieldSpec {
  name: WordFieldName
  label: string
  englishLabel: string
  max: number
  placeholder: string
  helper: string
}

export const WORD_FIELDS: readonly WordFieldSpec[] = [
  {
    name: 'word_form',
    label: '일본어 단어',
    englishLabel: 'Word',
    max: 50,
    placeholder: '例）懐かしい',
    helper: '한자, 히라가나 또는 가타카나',
  },
  {
    name: 'reading',
    label: '읽기',
    englishLabel: 'Reading',
    max: 100,
    placeholder: 'なつかしい',
    helper: '히라가나로 적어 주세요',
  },
  {
    name: 'meaning',
    label: '뜻',
    englishLabel: 'Meaning',
    max: 500,
    placeholder: '그리운, 그립다',
    helper: '문맥에서 떠올릴 핵심 뜻',
  },
] as const

export type WordValues = Record<WordFieldName, string>

export const EMPTY_WORD: WordValues = {
  word_form: '',
  reading: '',
  meaning: '',
}

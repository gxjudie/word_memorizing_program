// Typed client functions for each backend endpoint.
import { request, setToken } from './client'
import type {
  Difficulty,
  GeneratedSentence,
  LoginResponse,
  NoDueWords,
  RatingResult,
  ReviewSessionOut,
  WordEntryOut,
  WordInput,
} from './types'

// --- Auth -------------------------------------------------------------------

export async function login(
  email: string,
  password: string,
): Promise<LoginResponse> {
  const res = await request<LoginResponse>('/api/auth/login', {
    method: 'POST',
    body: { email, password },
    auth: false,
  })
  setToken(res.access_token)
  return res
}

export async function logout(): Promise<void> {
  try {
    await request<{ success: boolean }>('/api/auth/logout', { method: 'POST' })
  } finally {
    setToken(null)
  }
}

// --- Words ------------------------------------------------------------------

export function addWord(input: WordInput): Promise<WordEntryOut> {
  return request<WordEntryOut>('/api/words', { method: 'POST', body: input })
}

export function listWords(): Promise<WordEntryOut[]> {
  return request<WordEntryOut[]>('/api/words')
}

// --- Review session ---------------------------------------------------------

export function startSession(): Promise<ReviewSessionOut | NoDueWords> {
  return request<ReviewSessionOut | NoDueWords>('/api/review/sessions', {
    method: 'POST',
  })
}

export function generateSentence(
  sessionId: string,
  wordId: string,
): Promise<GeneratedSentence> {
  return request<GeneratedSentence>(
    `/api/review/sessions/${encodeURIComponent(sessionId)}/words/${encodeURIComponent(wordId)}/sentence`,
    { method: 'POST' },
  )
}

export function revealWord(
  sessionId: string,
  wordId: string,
): Promise<{ revealed: boolean }> {
  return request<{ revealed: boolean }>(
    `/api/review/sessions/${encodeURIComponent(sessionId)}/words/${encodeURIComponent(wordId)}/reveal`,
    { method: 'POST' },
  )
}

export function rateWord(
  sessionId: string,
  wordId: string,
  rating: Difficulty,
): Promise<RatingResult> {
  return request<RatingResult>(
    `/api/review/sessions/${encodeURIComponent(sessionId)}/words/${encodeURIComponent(wordId)}/rating`,
    { method: 'POST', body: { rating } },
  )
}

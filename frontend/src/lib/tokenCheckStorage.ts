import type { CheckTokenResponse } from '../api/types'

const STORAGE_KEY = 'nifty50_token_check'

interface StoredTokenCheck {
  session_date: string
  checked_at: string
  result: CheckTokenResponse
}

export function loadTokenCheck(sessionDate: string): StoredTokenCheck | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as StoredTokenCheck
    if (parsed.session_date !== sessionDate) return null
    return parsed
  } catch {
    return null
  }
}

export function saveTokenCheck(sessionDate: string, result: CheckTokenResponse): void {
  const payload: StoredTokenCheck = {
    session_date: sessionDate,
    checked_at: new Date().toISOString(),
    result,
  }
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
}

export function clearTokenCheck(): void {
  sessionStorage.removeItem(STORAGE_KEY)
}

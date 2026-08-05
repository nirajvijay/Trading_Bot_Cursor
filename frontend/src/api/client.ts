import type {
  AuthStatusResponse,
  CheckTokenResponse,
  LoginUrlResponse,
  ObservationReadiness,
  ObservationStartResponse,
  PreMarketChecklistResponse,
  GenerateResponse,
  RadarResponse,
  RunnerStatus,
  SessionCoverage,
  SessionResponse,
  SymbolTimelineResponse,
} from './types'

const BASE = '/api/v1'
const LOCAL_AUTH_HEADER = 'X-NIFTY-RADAR-LOCAL-AUTH'

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = typeof body.detail === 'string' ? body.detail : `API ${res.status}: ${path}`
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

async function postJson<T>(path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    const detail = typeof data.detail === 'string' ? data.detail : `API ${res.status}: ${path}`
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export function fetchSessions(): Promise<string[]> {
  return getJson<string[]>('/sessions')
}

export function fetchRadar(sessionDate: string): Promise<RadarResponse> {
  return getJson<RadarResponse>(`/sessions/${sessionDate}/radar`)
}

export function fetchSymbolTimeline(
  sessionDate: string,
  symbol: string,
): Promise<SymbolTimelineResponse> {
  return getJson<SymbolTimelineResponse>(
    `/sessions/${sessionDate}/symbols/${encodeURIComponent(symbol)}/timeline`,
  )
}

export function fetchCoverage(sessionDate: string): Promise<SessionCoverage> {
  return getJson<SessionCoverage>(`/sessions/${sessionDate}/coverage`)
}

export function fetchStatus(sessionDate: string): Promise<RunnerStatus> {
  return getJson<RunnerStatus>(`/sessions/${sessionDate}/status`)
}

export function fetchHealth(): Promise<{ status: string }> {
  return getJson<{ status: string }>('/health')
}

export function fetchAuthStatus(): Promise<AuthStatusResponse> {
  return getJson<AuthStatusResponse>('/auth/status')
}

export function fetchLoginUrl(): Promise<LoginUrlResponse> {
  return getJson<LoginUrlResponse>('/auth/login-url')
}

export function postSession(requestToken: string): Promise<SessionResponse> {
  return postJson<SessionResponse>(
    '/auth/session',
    { request_token: requestToken },
    { [LOCAL_AUTH_HEADER]: 'true' },
  )
}

export function postCheckToken(): Promise<CheckTokenResponse> {
  return postJson<CheckTokenResponse>('/auth/check-token')
}

export function fetchPreMarketChecklist(sessionDate?: string): Promise<PreMarketChecklistResponse> {
  const query = sessionDate ? `?session_date=${encodeURIComponent(sessionDate)}` : ''
  return getJson<PreMarketChecklistResponse>(`/premarket-checklist${query}`)
}

export function postGenerateLocalData(
  task: string,
  sessionDate?: string,
): Promise<GenerateResponse> {
  const query = sessionDate ? `?session_date=${encodeURIComponent(sessionDate)}` : ''
  return postJson<GenerateResponse>(
    `/premarket-checklist/generate/${encodeURIComponent(task)}${query}`,
  )
}

export function fetchObservationReadiness(sessionDate?: string): Promise<ObservationReadiness> {
  const query = sessionDate ? `?session_date=${encodeURIComponent(sessionDate)}` : ''
  return getJson<ObservationReadiness>(`/observation/readiness${query}`)
}

export function postStartObservation(sessionDate?: string): Promise<ObservationStartResponse> {
  const query = sessionDate ? `?session_date=${encodeURIComponent(sessionDate)}` : ''
  return postJson<ObservationStartResponse>(`/observation/start${query}`)
}

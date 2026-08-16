import type {
  AuthStatusResponse,
  CheckTokenResponse,
  KiteStartResponse,
  LoginUrlResponse,
  MeResponse,
  MfaSetupResponse,
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

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null
  const parts = document.cookie.split(';')
  for (const part of parts) {
    const [rawKey, ...rest] = part.trim().split('=')
    if (rawKey === name) {
      return decodeURIComponent(rest.join('='))
    }
  }
  return null
}

function csrfHeaders(): Record<string, string> {
  const token = readCookie('nr_csrf')
  return token ? { 'X-CSRF-Token': token } : {}
}

type AuthHandlers = {
  onUnauthorized?: () => void
}

let authHandlers: AuthHandlers = {}

export function setAuthHandlers(handlers: AuthHandlers) {
  authHandlers = handlers
}

async function handleResponse<T>(res: Response, path: string): Promise<T> {
  if (res.status === 401) {
    authHandlers.onUnauthorized?.()
    const body = await res.json().catch(() => ({}))
    const detail = typeof body.detail === 'string' ? body.detail : 'Authentication required'
    throw new ApiError(401, detail)
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = typeof body.detail === 'string' ? body.detail : `API ${res.status}: ${path}`
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) {
    return undefined as T
  }
  return res.json() as Promise<T>
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
  })
  return handleResponse<T>(res, path)
}

async function postJson<T>(path: string, body?: unknown, extraHeaders?: Record<string, string>): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...csrfHeaders(),
      ...extraHeaders,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  return handleResponse<T>(res, path)
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

export function fetchMe(): Promise<MeResponse> {
  return getJson<MeResponse>('/account/me')
}

export function postLogin(username: string, password: string, totp?: string): Promise<MeResponse> {
  return postJson<MeResponse>('/account/login', {
    username,
    password,
    ...(totp ? { totp } : {}),
  })
}

export function postLogout(): Promise<{ success: boolean; message: string }> {
  return postJson('/account/logout')
}

export function postStepUp(password: string, totp?: string): Promise<{ success: boolean; message: string }> {
  return postJson('/account/step-up', {
    password,
    ...(totp ? { totp } : {}),
  })
}

export function postMfaSetup(): Promise<MfaSetupResponse> {
  return postJson<MfaSetupResponse>('/account/mfa/setup', {})
}

export function postMfaConfirm(totp: string): Promise<{ success: boolean; message: string }> {
  return postJson('/account/mfa/confirm', { totp })
}

export function fetchAuthStatus(): Promise<AuthStatusResponse> {
  return getJson<AuthStatusResponse>('/auth/status')
}

export function fetchLoginUrl(): Promise<LoginUrlResponse> {
  return getJson<LoginUrlResponse>('/auth/login-url')
}

export function postKiteStart(): Promise<KiteStartResponse> {
  return postJson<KiteStartResponse>('/auth/kite/start')
}

export function postSession(requestToken: string): Promise<SessionResponse> {
  return postJson<SessionResponse>('/auth/session', { request_token: requestToken })
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

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  fetchAuthStatus,
  fetchLoginUrl,
  postCheckToken,
  postSession,
} from '../api/client'
import { formatTimeIst } from '../lib/format'
import type { AuthStatusResponse, CheckTokenResponse, SessionResponse } from '../api/types'
import { StatusBadge, type BadgeValue } from './ui/StatusBadge'
import { StatusField } from './ui/StatusField'

function maskUserId(userId: string | null | undefined): string {
  if (!userId) return '—'
  if (userId.length <= 2) return `${userId}****`
  return `${userId.slice(0, 2)}****`
}

function FlowStep({
  number,
  title,
  description,
  children,
  compact = false,
}: {
  number: string
  title: string
  description: string
  children: ReactNode
  compact?: boolean
}) {
  return (
    <div className="flex gap-2.5">
      <div className="w-6 h-6 shrink-0 rounded-full bg-primary text-white font-data text-[10px] font-semibold flex items-center justify-center">
        {number}
      </div>
      <div className={`flex-1 min-w-0 ${compact ? 'pb-2' : 'pb-3'}`}>
        <p className="text-xs font-semibold text-on-surface leading-tight">{title}</p>
        <p className="text-[11px] text-on-surface-variant mt-0.5 mb-1.5 leading-snug">{description}</p>
        {children}
      </div>
    </div>
  )
}

export function KiteAuthPage() {
  const [status, setStatus] = useState<AuthStatusResponse | null>(null)
  const [loginUrl, setLoginUrl] = useState<string | null>(null)
  const [requestToken, setRequestToken] = useState('')
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [lastSession, setLastSession] = useState<SessionResponse | null>(null)
  const [lastCheck, setLastCheck] = useState<CheckTokenResponse | null>(null)
  const [lastCheckedAt, setLastCheckedAt] = useState<string | null>(null)
  const [logMessage, setLogMessage] = useState('Awaiting input sequence...')

  const loadStatus = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchAuthStatus()
      setStatus(data)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load auth status')
      setLogMessage('Status load failed.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadStatus()
  }, [loadStatus])

  const tokenValid: BadgeValue = lastCheck
    ? lastCheck.valid
      ? 'YES'
      : 'NO'
    : 'UNKNOWN'

  const maskedUserId =
    lastCheck?.user_id != null
      ? maskUserId(lastCheck.user_id)
      : lastSession?.user_id != null
        ? maskUserId(lastSession.user_id)
        : '—'

  const maskedAccessPreview =
    status?.masked_access_token ?? lastSession?.masked_access_token ?? ''

  async function handleGenerateLoginUrl() {
    setActionLoading(true)
    setError(null)
    setSuccess(null)
    setLogMessage('Generating login URL...')
    try {
      const data = await fetchLoginUrl()
      setLoginUrl(data.login_url)
      setSuccess('Login URL generated.')
      setLogMessage('Login URL ready. Open Kite login in your browser.')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to generate login URL'
      setError(msg)
      setLogMessage(msg)
    } finally {
      setActionLoading(false)
    }
  }

  function handleOpenLogin() {
    if (loginUrl) {
      window.open(loginUrl, '_blank', 'noopener,noreferrer')
      setLogMessage('Kite login opened in new tab.')
    }
  }

  async function handleGenerateSession() {
    const trimmed = requestToken.trim()
    if (!trimmed) {
      setError('Paste a request_token or full redirect URL first.')
      setLogMessage('Token input required.')
      return
    }
    setActionLoading(true)
    setError(null)
    setSuccess(null)
    setLastSession(null)
    setLogMessage('Exchanging request token...')
    try {
      const data = await postSession(trimmed)
      setLastSession(data)
      setSuccess(data.message)
      setRequestToken('')
      setLastCheck(null)
      setLastCheckedAt(null)
      setLogMessage('Access token saved to backend/.env.')
      await loadStatus()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to generate access token'
      setError(msg)
      setLogMessage(msg)
    } finally {
      setActionLoading(false)
    }
  }

  async function handleCheckToken() {
    setActionLoading(true)
    setError(null)
    setSuccess(null)
    setLogMessage('Validating access token...')
    try {
      const data = await postCheckToken()
      setLastCheck(data)
      setLastCheckedAt(new Date().toISOString())
      if (data.valid) {
        setSuccess(data.message)
        setLogMessage('Access token is valid.')
      } else {
        setError(data.message)
        setLogMessage(data.message)
      }
      await loadStatus()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to check access token'
      setError(msg)
      setLogMessage(msg)
    } finally {
      setActionLoading(false)
    }
  }

  function handleClear() {
    setRequestToken('')
    setLoginUrl(null)
    setError(null)
    setSuccess(null)
    setLastSession(null)
    setLastCheck(null)
    setLastCheckedAt(null)
    setLogMessage('Input sequence cleared.')
  }

  const alertMessage = error ?? success

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden bg-background">
      <div className="shrink-0 px-5 py-3 border-b border-outline-variant bg-white">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-9 h-9 rounded bg-secondary-container flex items-center justify-center shrink-0">
              <span className="material-symbols-outlined text-primary text-[20px]">key</span>
            </div>
            <div className="min-w-0">
              <h1 className="text-base font-bold tracking-tight text-on-surface truncate">
                Kite Auth Configuration
              </h1>
              <p className="text-xs text-on-surface-variant truncate">
                System authentication bridge for Zerodha Kite Connect API
              </p>
            </div>
          </div>
          <span className="label-caps px-2 py-0.5 bg-emerald-50 text-positive border border-emerald-200 rounded-sm shrink-0">
            Local only
          </span>
        </div>
      </div>

      {alertMessage && (
        <div
          className={`shrink-0 mx-4 mt-2 px-3 py-1.5 text-xs border ${
            error
              ? 'bg-red-50 border-red-200 text-red-800'
              : 'bg-emerald-50 border-emerald-200 text-positive'
          }`}
        >
          {alertMessage}
        </div>
      )}

      <div className="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-2 gap-3 p-4 overflow-hidden">
        <section className="bg-white border border-outline-variant flex flex-col min-h-0 overflow-hidden">
          <div className="shrink-0 px-3 py-2 border-b border-outline-variant bg-surface-container-low">
            <h2 className="label-caps text-on-surface">System auth status</h2>
          </div>
          <div className="flex-1 min-h-0 px-3 py-1 overflow-hidden">
            {loading && !status ? (
              <p className="text-xs text-on-surface-variant py-2">Loading status...</p>
            ) : status ? (
              <>
                <StatusField label="API key configured">
                  <StatusBadge value={status.api_key_configured ? 'YES' : 'NO'} />
                </StatusField>
                <StatusField label="API secret configured">
                  <StatusBadge value={status.api_secret_configured ? 'YES' : 'NO'} />
                </StatusField>
                <StatusField label="Access token present">
                  <StatusBadge value={status.access_token_present ? 'YES' : 'NO'} />
                </StatusField>
                <StatusField label="Access token valid">
                  <StatusBadge value={tokenValid} />
                </StatusField>
                <StatusField label="Last checked (IST)">
                  <span className="font-data text-[10px]">
                    {lastCheckedAt ? `${formatTimeIst(lastCheckedAt)} IST` : '—'}
                  </span>
                </StatusField>
                <StatusField label="Masked user ID">
                  <span className="font-data text-[10px]">{maskedUserId}</span>
                </StatusField>
                <StatusField label="Masked access token">
                  <input
                    readOnly
                    value={maskedAccessPreview || '—'}
                    className="font-data text-[10px] w-36 text-right bg-surface-container-low border border-outline-variant px-1.5 py-0.5 text-on-surface-variant"
                  />
                </StatusField>
              </>
            ) : null}
          </div>
          <div className="shrink-0 px-3 pb-3 pt-1">
            <button
              type="button"
              className="w-full border-2 border-primary text-primary bg-white py-2 rounded label-caps font-bold hover:bg-secondary-container/30 disabled:opacity-50 flex items-center justify-center gap-2 text-[10px]"
              onClick={() => void handleCheckToken()}
              disabled={actionLoading || loading}
            >
              <span className="material-symbols-outlined text-[16px]">verified_user</span>
              Check access token
            </button>
            {!status?.access_token_present && (
              <p className="mt-2 text-[10px] text-on-surface-variant leading-snug">
                No active session. Complete the login flow to initialize market data streams.
              </p>
            )}
          </div>
        </section>

        <div className="flex flex-col min-h-0 gap-3 overflow-hidden">
          <section className="shrink-0 bg-white border-2 border-primary/30 px-3 py-2.5">
            <div className="flex items-center gap-2 mb-1">
              <span className="material-symbols-outlined text-primary text-[18px]">shield</span>
              <h2 className="text-xs font-bold text-on-surface">Safety protocol</h2>
            </div>
            <ul className="text-[10px] text-on-surface-variant space-y-0.5 list-disc pl-4 leading-snug">
              <li>Updates local backend/.env only. Does not place orders.</li>
              <li>API secrets are never shown. Tokens are masked in the UI.</li>
            </ul>
          </section>

          <section className="bg-white border border-outline-variant flex flex-col flex-1 min-h-0 overflow-hidden">
            <div className="shrink-0 px-3 py-2 border-b border-outline-variant bg-surface-container-low flex items-center justify-between gap-2">
              <h2 className="label-caps text-on-surface">Kite login sequence flow</h2>
              <button
                type="button"
                onClick={handleClear}
                className="text-[10px] label-caps text-on-surface-variant hover:text-primary"
                disabled={actionLoading}
              >
                Clear
              </button>
            </div>
            <div className="flex-1 min-h-0 flex flex-col p-3 overflow-hidden">
              <div className="flex-1 min-h-0 overflow-hidden">
                <FlowStep
                  number="01"
                  title="Session initialization"
                  description="Generate the Kite Connect login URL."
                  compact
                >
                  <button
                    type="button"
                    className="bg-primary text-white px-3 py-1.5 rounded label-caps font-bold hover:opacity-90 disabled:opacity-50 text-[10px]"
                    onClick={() => void handleGenerateLoginUrl()}
                    disabled={actionLoading}
                  >
                    Generate login URL
                  </button>
                  <input
                    readOnly
                    value={loginUrl ?? ''}
                    placeholder="Login URL will appear here..."
                    className="mt-1.5 w-full font-data text-[10px] bg-surface-container-low border border-outline-variant px-2 py-1 text-on-surface-variant"
                  />
                </FlowStep>

                <FlowStep
                  number="02"
                  title="Interactive authentication"
                  description="Open Zerodha login in your browser."
                  compact
                >
                  <button
                    type="button"
                    className="border border-outline-variant bg-white px-3 py-1.5 rounded label-caps flex items-center gap-1.5 hover:bg-surface-container-low disabled:opacity-50 text-[10px]"
                    onClick={handleOpenLogin}
                    disabled={!loginUrl || actionLoading}
                  >
                    <span className="material-symbols-outlined text-[14px]">open_in_new</span>
                    Open Kite login
                  </button>
                </FlowStep>

                <FlowStep
                  number="03"
                  title="Token capture and finalize"
                  description="Paste request_token or full redirect URL."
                  compact
                >
                  <textarea
                    className="w-full h-14 font-data text-[10px] bg-surface-container-low border border-outline-variant px-2 py-1.5 mb-2 resize-none"
                    placeholder="Paste full redirect URL or token here..."
                    value={requestToken}
                    onChange={(e) => setRequestToken(e.target.value)}
                  />
                  <button
                    type="button"
                    className="w-full bg-primary text-white py-2 rounded label-caps font-bold hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2 text-[10px]"
                    onClick={() => void handleGenerateSession()}
                    disabled={actionLoading}
                  >
                    <span className="material-symbols-outlined text-[16px]">sync</span>
                    Generate access token
                  </button>
                </FlowStep>
              </div>

              <p className="shrink-0 font-data text-[10px] text-on-surface-variant border-t border-outline-variant pt-2 truncate">
                * Log: {logMessage}
              </p>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}

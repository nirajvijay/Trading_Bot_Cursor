import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import {
  fetchAuthStatus,
  fetchLoginUrl,
  postCheckToken,
  postKiteStart,
  postSession,
  postStepUp,
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

function kiteBannerFromQuery(): { kind: 'ok' | 'error'; text: string } | null {
  if (typeof window === 'undefined') return null
  const params = new URLSearchParams(window.location.search)
  const kite = params.get('kite')
  if (kite === 'connected') {
    return { kind: 'ok', text: 'Kite connected. Market-data token saved on the server.' }
  }
  if (kite === 'error') {
    return { kind: 'error', text: 'Kite login failed. Existing token was not changed.' }
  }
  return null
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
  const [showPaste, setShowPaste] = useState(false)
  const [showStepUp, setShowStepUp] = useState(false)
  const [stepUpPassword, setStepUpPassword] = useState('')
  const [stepUpTotp, setStepUpTotp] = useState('')
  const [pendingAfterStepUp, setPendingAfterStepUp] = useState<'kite-start' | 'paste' | null>(null)
  const [kiteBanner, setKiteBanner] = useState(kiteBannerFromQuery)

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

  useEffect(() => {
    if (!kiteBanner) return
    // Strip kite query params from the address bar without reload.
    const url = new URL(window.location.href)
    if (url.searchParams.has('kite')) {
      url.searchParams.delete('kite')
      window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`)
    }
  }, [kiteBanner])

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

  async function runKiteStart() {
    setActionLoading(true)
    setError(null)
    setSuccess(null)
    setLogMessage('Starting remote Kite login...')
    try {
      const data = await postKiteStart()
      setLogMessage('Redirecting to Kite...')
      window.location.assign(data.authorize_url)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to start Kite login'
      setError(msg)
      setLogMessage(msg)
      if (msg.toLowerCase().includes('step-up')) {
        setShowStepUp(true)
      }
      setActionLoading(false)
    }
  }

  async function handleStartKiteLogin() {
    setPendingAfterStepUp('kite-start')
    setShowStepUp(true)
  }

  async function finalizePasteLogin() {
    const trimmed = requestToken.trim()
    if (!trimmed) return
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
      setLogMessage('Access token saved to secrets store.')
      await loadStatus()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to generate access token'
      setError(msg)
      setLogMessage(msg)
    } finally {
      setActionLoading(false)
    }
  }

  async function handleStepUpSubmit(event: FormEvent) {
    event.preventDefault()
    setActionLoading(true)
    setError(null)
    try {
      await postStepUp(stepUpPassword, stepUpTotp.trim() || undefined)
      const next = pendingAfterStepUp
      setShowStepUp(false)
      setStepUpPassword('')
      setStepUpTotp('')
      setPendingAfterStepUp(null)
      if (next === 'paste') {
        setActionLoading(false)
        await finalizePasteLogin()
      } else {
        await runKiteStart()
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Step-up failed'
      setError(msg)
      setLogMessage(msg)
      setActionLoading(false)
    }
  }

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
    setPendingAfterStepUp('paste')
    setShowStepUp(true)
    setLogMessage('Complete step-up to finalize paste login.')
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
                Daily market-data token for Zerodha Kite Connect (observation only)
              </p>
            </div>
          </div>
        </div>
      </div>

      {kiteBanner && (
        <div
          className={`shrink-0 mx-4 mt-2 px-3 py-1.5 text-xs border ${
            kiteBanner.kind === 'error'
              ? 'bg-red-50 border-red-200 text-red-800'
              : 'bg-emerald-50 border-emerald-200 text-positive'
          }`}
        >
          {kiteBanner.text}
          <button
            type="button"
            className="ml-2 underline"
            onClick={() => setKiteBanner(null)}
          >
            Dismiss
          </button>
        </div>
      )}

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

      {showStepUp && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
          <form
            onSubmit={(e) => void handleStepUpSubmit(e)}
            className="w-full max-w-sm bg-white border border-outline-variant p-4 space-y-3"
          >
            <h2 className="text-sm font-bold text-on-surface">Confirm step-up</h2>
            <p className="text-[11px] text-on-surface-variant">
              Password{status?.access_token_present ? '' : ''} and MFA (if enabled) required before
              changing the Kite token.
            </p>
            <input
              type="password"
              className="w-full border border-outline-variant px-2 py-1.5 text-sm"
              placeholder="Password"
              value={stepUpPassword}
              onChange={(e) => setStepUpPassword(e.target.value)}
              required
            />
            <input
              className="w-full border border-outline-variant px-2 py-1.5 text-sm font-data"
              placeholder="MFA code (if enabled)"
              value={stepUpTotp}
              onChange={(e) => setStepUpTotp(e.target.value)}
            />
            <div className="flex gap-2 justify-end">
              <button
                type="button"
                className="px-3 py-1.5 text-[10px] label-caps border border-outline-variant"
                onClick={() => setShowStepUp(false)}
                disabled={actionLoading}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="px-3 py-1.5 text-[10px] label-caps bg-primary text-white font-bold disabled:opacity-50"
                disabled={actionLoading}
              >
                Confirm
              </button>
            </div>
          </form>
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
          <div className="shrink-0 px-3 pb-3 pt-1 space-y-2">
            <button
              type="button"
              className="w-full bg-primary text-white py-2 rounded label-caps font-bold hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2 text-[10px]"
              onClick={() => void handleStartKiteLogin()}
              disabled={actionLoading || loading}
            >
              <span className="material-symbols-outlined text-[16px]">login</span>
              Start Kite Login
            </button>
            <button
              type="button"
              className="w-full border-2 border-primary text-primary bg-white py-2 rounded label-caps font-bold hover:bg-secondary-container/30 disabled:opacity-50 flex items-center justify-center gap-2 text-[10px]"
              onClick={() => void handleCheckToken()}
              disabled={actionLoading || loading}
            >
              <span className="material-symbols-outlined text-[16px]">verified_user</span>
              Check access token
            </button>
          </div>
        </section>

        <div className="flex flex-col min-h-0 gap-3 overflow-hidden">
          <section className="shrink-0 bg-white border-2 border-primary/30 px-3 py-2.5">
            <div className="flex items-center gap-2 mb-1">
              <span className="material-symbols-outlined text-primary text-[18px]">shield</span>
              <h2 className="text-xs font-bold text-on-surface">Safety protocol</h2>
            </div>
            <ul className="text-[10px] text-on-surface-variant space-y-0.5 list-disc pl-4 leading-snug">
              <li>Updates server secrets store only. Does not place orders.</li>
              <li>API secrets and raw tokens are never shown in the UI.</li>
              <li>Token changes require website step-up authentication.</li>
            </ul>
          </section>

          <section className="bg-white border border-outline-variant flex flex-col flex-1 min-h-0 overflow-hidden">
            <div className="shrink-0 px-3 py-2 border-b border-outline-variant bg-surface-container-low flex items-center justify-between gap-2">
              <h2 className="label-caps text-on-surface">Legacy paste login (optional)</h2>
              <button
                type="button"
                onClick={() => setShowPaste((v) => !v)}
                className="text-[10px] label-caps text-on-surface-variant hover:text-primary"
              >
                {showPaste ? 'Hide' : 'Show'}
              </button>
            </div>
            {showPaste ? (
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
                    description="Paste request_token or full redirect URL (dev/non-prod)."
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
                    <button
                      type="button"
                      onClick={handleClear}
                      className="mt-2 text-[10px] label-caps text-on-surface-variant hover:text-primary"
                      disabled={actionLoading}
                    >
                      Clear
                    </button>
                  </FlowStep>
                </div>

                <p className="shrink-0 font-data text-[10px] text-on-surface-variant border-t border-outline-variant pt-2 truncate">
                  * Log: {logMessage}
                </p>
              </div>
            ) : (
              <div className="p-3 text-[11px] text-on-surface-variant">
                Prefer <strong>Start Kite Login</strong>. Paste login remains available for
                non-production recovery when enabled on the server.
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}

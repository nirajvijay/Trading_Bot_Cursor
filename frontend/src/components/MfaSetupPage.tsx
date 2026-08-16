import { useState, type FormEvent } from 'react'
import { ApiError, postMfaConfirm, postMfaSetup } from '../api/client'
import type { MfaSetupResponse } from '../api/types'

interface Props {
  username: string
  onCompleted: () => void
  onLogout: () => void
}

export function MfaSetupPage({ username, onCompleted, onLogout }: Props) {
  const [setup, setSetup] = useState<MfaSetupResponse | null>(null)
  const [totp, setTotp] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  async function handleStart() {
    setBusy(true)
    setError(null)
    try {
      const data = await postMfaSetup()
      setSetup(data)
      setTotp('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'MFA setup failed')
    } finally {
      setBusy(false)
    }
  }

  async function handleConfirm(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await postMfaConfirm(totp.trim())
      onCompleted()
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Confirm failed'
      setError(message)
    } finally {
      setBusy(false)
    }
  }

  async function copySecret() {
    if (!setup?.secret) return
    try {
      await navigator.clipboard.writeText(setup.secret)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setError('Could not copy secret — select and copy it manually.')
    }
  }

  return (
    <div className="flex h-full items-center justify-center bg-background px-4">
      <div className="w-full max-w-md bg-white border border-outline-variant px-5 py-6 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-bold text-on-surface tracking-tight">Enable MFA</h1>
            <p className="text-xs text-on-surface-variant mt-1">
              Owner <span className="font-data">{username}</span> — enrol TOTP before production
              cutover. Secrets stay in this browser session only.
            </p>
          </div>
          <button
            type="button"
            className="label-caps px-2 py-1 border border-outline-variant text-on-surface-variant text-[10px] shrink-0"
            onClick={onLogout}
          >
            Logout
          </button>
        </div>

        {error && (
          <div className="px-3 py-2 text-xs bg-red-50 border border-red-200 text-red-800">{error}</div>
        )}

        {!setup ? (
          <div className="space-y-3">
            <p className="text-xs text-on-surface-variant">
              Step 1: generate a TOTP secret (CSRF-protected). Scan or enter it in your authenticator
              app, then confirm with a code.
            </p>
            <button
              type="button"
              disabled={busy}
              onClick={() => void handleStart()}
              className="w-full bg-primary text-white py-2 label-caps font-bold hover:opacity-90 disabled:opacity-50"
            >
              {busy ? 'Starting…' : 'Start MFA setup'}
            </button>
          </div>
        ) : (
          <form onSubmit={(e) => void handleConfirm(e)} className="space-y-3">
            <p className="text-xs text-on-surface-variant">{setup.message}</p>
            <label className="block space-y-1">
              <span className="label-caps text-on-surface-variant">Secret (enter in authenticator)</span>
              <div className="flex gap-2">
                <input
                  readOnly
                  className="w-full border border-outline-variant bg-surface-container-low px-2 py-1.5 text-xs font-data"
                  value={setup.secret}
                />
                <button
                  type="button"
                  className="label-caps px-2 py-1 border border-outline-variant text-[10px] shrink-0"
                  onClick={() => void copySecret()}
                >
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
            </label>
            <label className="block space-y-1">
              <span className="label-caps text-on-surface-variant">otpauth URI (optional scan)</span>
              <textarea
                readOnly
                rows={3}
                className="w-full border border-outline-variant bg-surface-container-low px-2 py-1.5 text-[10px] font-data break-all"
                value={setup.otpauth_uri}
              />
            </label>
            <label className="block space-y-1">
              <span className="label-caps text-on-surface-variant">Current 6-digit code</span>
              <input
                className="w-full border border-outline-variant bg-surface-container-low px-2 py-1.5 text-sm font-data"
                value={totp}
                onChange={(e) => setTotp(e.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                minLength={6}
                maxLength={8}
                placeholder="123456"
              />
            </label>
            <button
              type="submit"
              disabled={busy || totp.trim().length < 6}
              className="w-full bg-primary text-white py-2 label-caps font-bold hover:opacity-90 disabled:opacity-50"
            >
              {busy ? 'Confirming…' : 'Confirm MFA'}
            </button>
            <button
              type="button"
              disabled={busy}
              className="w-full label-caps text-[10px] text-on-surface-variant underline"
              onClick={() => void handleStart()}
            >
              Regenerate secret
            </button>
          </form>
        )}
      </div>
    </div>
  )
}

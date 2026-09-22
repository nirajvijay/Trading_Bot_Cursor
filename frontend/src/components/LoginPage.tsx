import { useEffect, useState, type FormEvent } from 'react'
import { describePasskeyError, isPlatformAuthenticatorAvailable } from '../lib/passkey'

const LAST_USERNAME_KEY = 'nr_last_username'

function readLastUsername(): string {
  try {
    return window.localStorage.getItem(LAST_USERNAME_KEY) ?? ''
  } catch {
    // Private windows and blocked site data both throw here. A remembered
    // username is a convenience, never a requirement, so swallow and carry on.
    return ''
  }
}

function rememberUsername(username: string): void {
  try {
    window.localStorage.setItem(LAST_USERNAME_KEY, username)
  } catch {
    /* see readLastUsername */
  }
}

interface Props {
  onLogin: (username: string, password: string, totp?: string) => Promise<void>
  onPasskeyLogin: (username: string, password: string) => Promise<void>
}

export function LoginPage({ onLogin, onPasskeyLogin }: Props) {
  const [username, setUsername] = useState(readLastUsername)
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [passkeyLoading, setPasskeyLoading] = useState(false)
  // null while the capability check is still in flight, so the primary button
  // is not swapped out underneath a user who is already typing.
  const [platformAvailable, setPlatformAvailable] = useState<boolean | null>(null)
  const [recoveryMode, setRecoveryMode] = useState(false)

  useEffect(() => {
    let cancelled = false
    void isPlatformAuthenticatorAvailable().then((available) => {
      if (cancelled) return
      setPlatformAvailable(available)
      // No Touch ID on this machine => the authenticator code is the only way
      // in, so open that path immediately instead of hiding it behind a link.
      if (!available) setRecoveryMode(true)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const touchIdReady = platformAvailable === true
  const busy = loading || passkeyLoading
  const canSubmit = Boolean(username.trim() && password)

  async function handleTotpLogin(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    setError(null)
    try {
      await onLogin(username.trim(), password, totp.trim() || undefined)
      rememberUsername(username.trim())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  async function handlePasskeyLogin(event: FormEvent) {
    event.preventDefault()
    setPasskeyLoading(true)
    setError(null)
    try {
      await onPasskeyLogin(username.trim(), password)
      rememberUsername(username.trim())
    } catch (err) {
      setError(describePasskeyError(err, 'Touch ID sign-in failed'))
    } finally {
      setPasskeyLoading(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center bg-background px-4">
      <form
        onSubmit={(e) => void (recoveryMode ? handleTotpLogin(e) : handlePasskeyLogin(e))}
        className="w-full max-w-sm bg-white border border-outline-variant px-5 py-6 space-y-4"
      >
        <div>
          <h1 className="text-lg font-bold text-on-surface tracking-tight">NIFTY RADAR</h1>
          <p className="text-xs text-on-surface-variant mt-1">
            Sign in to the observation dashboard
          </p>
        </div>
        {error && (
          <div className="px-3 py-2 text-xs bg-red-50 border border-red-200 text-red-800">
            {error}
          </div>
        )}
        <label className="block space-y-1">
          <span className="label-caps text-on-surface-variant">Username</span>
          <input
            className="w-full border border-outline-variant bg-surface-container-low px-2 py-1.5 text-sm"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username webauthn"
            required
          />
        </label>
        <label className="block space-y-1">
          <span className="label-caps text-on-surface-variant">Password</span>
          <input
            type="password"
            className="w-full border border-outline-variant bg-surface-container-low px-2 py-1.5 text-sm"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        {recoveryMode && (
          <label className="block space-y-1">
            <span className="label-caps text-on-surface-variant">Authenticator code</span>
            <input
              className="w-full border border-outline-variant bg-surface-container-low px-2 py-1.5 text-sm font-data"
              value={totp}
              onChange={(e) => setTotp(e.target.value)}
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="6-digit code from your phone"
              autoFocus
            />
          </label>
        )}

        {recoveryMode ? (
          <button
            type="submit"
            disabled={busy || !canSubmit}
            className="w-full bg-primary text-white py-2 label-caps font-bold hover:opacity-90 disabled:opacity-50"
          >
            {loading ? 'Signing in...' : 'Sign in with code'}
          </button>
        ) : (
          <button
            type="submit"
            disabled={busy || !canSubmit || !touchIdReady}
            className="w-full bg-primary text-white py-2 label-caps font-bold hover:opacity-90 disabled:opacity-50"
          >
            {passkeyLoading ? 'Waiting for Touch ID...' : 'Sign in with Touch ID'}
          </button>
        )}

        {touchIdReady && (
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setError(null)
              setTotp('')
              setRecoveryMode((value) => !value)
            }}
            className="w-full label-caps text-[10px] text-on-surface-variant underline disabled:opacity-50"
          >
            {recoveryMode ? 'Use Touch ID instead' : 'Use authenticator code instead'}
          </button>
        )}

        <p className="text-[10px] leading-4 text-on-surface-variant">
          {recoveryMode
            ? 'The authenticator code is the recovery path — it needs your phone.'
            : 'Touch ID replaces the authenticator code, so your phone stays in your pocket.'}
        </p>
      </form>
    </div>
  )
}

import { useEffect, useState, type FormEvent } from 'react'
import { marketStatusNow, todayIst } from '../lib/format'
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

function istClock(now: Date): string {
  return now.toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour12: false })
}

/** Dark brand panel. Design calls this the "Session only" panelContent variant:
 *  no image slot, because there is no hero image to put in it yet. */
function StationPanel({ clock }: { clock: string }) {
  const rows: [string, string][] = [
    ['Session', todayIst()],
    ['IST', clock],
    ['Auth', 'Password · TOTP · Passkey'],
  ]
  return (
    <aside className="hidden md:flex flex-col justify-between gap-8 bg-on-surface p-7 text-slate-200">
      <div className="flex flex-col gap-2.5">
        <span className="label-caps text-slate-400">Observation station</span>
        <h1 className="m-0 text-[26px] font-extrabold tracking-[-0.02em] text-white">NIFTY RADAR</h1>
        <p className="m-0 text-[13px] leading-5 text-slate-300 text-pretty">
          Owner access to the observation dashboard, trading engine and admin console.
        </p>
      </div>
      <div className="flex flex-col border-t border-slate-700">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-3 border-b border-slate-700 py-2.5">
            <span className="label-caps text-slate-400">{label}</span>
            <span className="font-data text-xs font-medium text-white">{value}</span>
          </div>
        ))}
      </div>
    </aside>
  )
}

interface Props {
  onLogin: (username: string, password: string, totp?: string) => Promise<void>
  onPasskeyLogin: (username: string, password: string) => Promise<void>
}

export function LoginPage({ onLogin, onPasskeyLogin }: Props) {
  const [username, setUsername] = useState(readLastUsername)
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [passkeyLoading, setPasskeyLoading] = useState(false)
  // null while the capability check is still in flight, so the primary button
  // is not swapped out underneath a user who is already typing.
  const [platformAvailable, setPlatformAvailable] = useState<boolean | null>(null)
  const [recoveryMode, setRecoveryMode] = useState(false)
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(id)
  }, [])

  useEffect(() => {
    let cancelled = false
    void isPlatformAuthenticatorAvailable().then((available) => {
      if (cancelled) return
      setPlatformAvailable(available)
      // No Touch ID on this machine => the authenticator code is the only way
      // in, so open that path immediately instead of hiding it behind a button.
      if (!available) setRecoveryMode(true)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const touchIdReady = platformAvailable === true
  const busy = loading || passkeyLoading
  const canSubmit = Boolean(username.trim() && password)
  const marketOpen = marketStatusNow() === 'OPEN'

  async function handleTotpLogin() {
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

  async function handlePasskeyLogin() {
    setPasskeyLoading(true)
    setError(null)
    try {
      await onPasskeyLogin(username.trim(), password)
      rememberUsername(username.trim())
    } catch (err) {
      const message = describePasskeyError(err, 'Touch ID sign-in failed')
      // First run, and after every passkey is removed, there is nothing to
      // authenticate against. Open the recovery path rather than leaving the
      // only way forward behind a button the user has to reason about.
      if (/no passkey is enrolled/i.test(message)) {
        setRecoveryMode(true)
        setError('No passkey registered on this Mac yet — sign in with your code, then register it.')
      } else {
        setError(message)
      }
    } finally {
      setPasskeyLoading(false)
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    void (recoveryMode ? handleTotpLogin() : handlePasskeyLogin())
  }

  const fieldClass =
    'h-9 box-border w-full border border-outline-variant bg-surface-container-low px-2.5 text-sm text-on-surface focus:border-primary focus:bg-white focus:outline-none'
  const buttonClass =
    'h-[38px] w-full border border-primary label-caps text-[11px] font-bold disabled:opacity-50'

  return (
    <div className="flex h-full flex-col bg-background">
      <header className="flex h-10 shrink-0 items-center gap-4 border-b border-outline-variant bg-white px-4">
        <span className="text-sm font-extrabold uppercase tracking-[-0.01em]">
          NIFTY 100 live strategy table
        </span>
        <div className="hidden sm:block h-4 w-px bg-outline-variant" />
        <div className="hidden sm:flex items-center gap-1.5">
          <span className="label-caps text-on-surface-variant">Market status:</span>
          <span className={`label-caps flex items-center gap-1 ${marketOpen ? 'text-positive' : 'text-on-surface-variant'}`}>
            <span
              className={`size-1.5 rounded-full ${marketOpen ? 'bg-positive animate-pulse' : 'bg-on-surface-variant'}`}
            />
            {marketOpen ? 'Open' : 'Closed'}
          </span>
        </div>
      </header>

      <div className="flex flex-1 items-center justify-center overflow-auto p-6">
        <div className="grid w-full max-w-[400px] border border-outline-variant bg-white md:w-[740px] md:max-w-none md:grid-cols-[340px_400px]">
          <StationPanel clock={istClock(now)} />

          <form onSubmit={handleSubmit} className="flex flex-col gap-4 p-7">
            <div className="flex flex-col gap-1">
              <h2 className="m-0 text-lg font-bold tracking-[-0.01em]">Sign in</h2>
              <p className="m-0 text-xs text-on-surface-variant">Use your owner credentials.</p>
            </div>

            {error && (
              <div className="flex items-center gap-2 border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
                <span className="material-symbols-outlined text-base leading-none">error</span>
                {error}
              </div>
            )}

            <label className="flex flex-col gap-1.5">
              <span className="label-caps text-on-surface-variant">Username</span>
              <input
                className={fieldClass}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username webauthn"
                required
              />
            </label>

            <label className="flex flex-col gap-1.5">
              <span className="flex justify-between">
                <span className="label-caps text-on-surface-variant">Password</span>
                <button
                  type="button"
                  onClick={() => setShowPassword((value) => !value)}
                  className="label-caps cursor-pointer text-primary"
                >
                  {showPassword ? 'Hide' : 'Show'}
                </button>
              </span>
              <input
                type={showPassword ? 'text' : 'password'}
                className={fieldClass}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </label>

            {recoveryMode && (
              <label className="flex flex-col gap-1.5">
                <span className="flex justify-between">
                  <span className="label-caps text-on-surface-variant">MFA code</span>
                  <span className="label-caps text-slate-400">If enabled</span>
                </span>
                <input
                  className={`${fieldClass} font-data text-[15px] font-medium tracking-[.3em]`}
                  value={totp}
                  onChange={(e) => setTotp(e.target.value.replace(/\D/g, ''))}
                  inputMode="numeric"
                  maxLength={6}
                  autoComplete="one-time-code"
                  placeholder="000 000"
                  autoFocus
                />
              </label>
            )}

            <div className="flex flex-col gap-4">
              <button
                type="submit"
                disabled={busy || !canSubmit || (!recoveryMode && !touchIdReady)}
                className={`${buttonClass} order-0 flex items-center justify-center gap-2 bg-primary text-white hover:opacity-90`}
              >
                {recoveryMode ? (
                  loading ? 'Signing in…' : 'Sign in'
                ) : (
                  <>
                    <span className="material-symbols-outlined text-[18px] leading-none tracking-normal normal-case">
                      fingerprint
                    </span>
                    {passkeyLoading ? 'Waiting for Touch ID…' : 'Sign in with Touch ID'}
                  </>
                )}
              </button>

              {touchIdReady && (
                <>
                  <div className="order-1 flex items-center gap-2.5">
                    <div className="h-px flex-1 bg-outline-variant" />
                    <span className="label-caps text-slate-400">or</span>
                    <div className="h-px flex-1 bg-outline-variant" />
                  </div>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setError(null)
                      setTotp('')
                      setRecoveryMode((value) => !value)
                    }}
                    className={`${buttonClass} order-2 bg-white text-primary hover:bg-sky-50`}
                  >
                    {recoveryMode ? 'Use Touch ID instead' : 'Use authenticator code'}
                  </button>
                </>
              )}
            </div>

            <p className="m-0 text-[11px] leading-4 text-on-surface-variant text-pretty">
              {recoveryMode
                ? 'The authenticator code is the recovery path — it needs your phone.'
                : 'Touch ID needs your username and password first. Your authenticator code stays available as recovery.'}
            </p>
          </form>
        </div>
      </div>

      <footer className="flex h-7 shrink-0 items-center justify-center border-t border-outline-variant bg-white label-caps text-on-surface-variant">
        Owner only access
      </footer>
    </div>
  )
}

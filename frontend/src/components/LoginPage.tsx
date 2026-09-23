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

type Mode = 'touch' | 'password'

/** Dark brand panel. No image slot -- there is no hero image for it yet.
 *  Auth row highlights whichever method is in play; "This device" surfaces
 *  the platform-authenticator check that decides whether Touch ID can even
 *  be offered, so a machine without it doesn't look like a random dead end. */
function StationPanel({
  clock,
  mode,
  platformAvailable,
}: {
  clock: string
  mode: Mode
  platformAvailable: boolean | null
}) {
  const activeCls = 'text-white'
  const inactiveCls = 'text-slate-500'
  const deviceLabel =
    platformAvailable === null ? 'Checking…' : platformAvailable ? 'Touch ID ready' : 'No Touch ID'
  const deviceCls =
    platformAvailable === null ? 'text-slate-500' : platformAvailable ? 'text-white' : 'text-amber-400'

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
        <div className="flex justify-between gap-3 border-b border-slate-700 py-2.5">
          <span className="label-caps text-slate-400">Session</span>
          <span className="font-data text-xs font-medium text-white">{todayIst()}</span>
        </div>
        <div className="flex justify-between gap-3 border-b border-slate-700 py-2.5">
          <span className="label-caps text-slate-400">IST</span>
          <span className="font-data text-xs font-medium text-white">{clock}</span>
        </div>
        <div className="flex justify-between gap-3 border-b border-slate-700 py-2.5">
          <span className="label-caps text-slate-400">Auth</span>
          <span className="font-data text-xs font-medium flex gap-1.5">
            <span className={mode === 'touch' ? activeCls : inactiveCls}>Passkey</span>
            <span className="text-slate-600">·</span>
            <span className={mode === 'password' ? activeCls : inactiveCls}>Password</span>
            <span className="text-slate-600">·</span>
            <span className={mode === 'password' ? activeCls : inactiveCls}>TOTP</span>
          </span>
        </div>
        <div className="flex justify-between gap-3 py-2.5">
          <span className="label-caps text-slate-400">This device</span>
          <span className={`font-data text-xs font-medium ${deviceCls}`}>{deviceLabel}</span>
        </div>
      </div>
    </aside>
  )
}

interface Props {
  onLogin: (username: string, password: string, totp?: string) => Promise<void>
  onPasskeyLogin: () => Promise<void>
}

export function LoginPage({ onLogin, onPasskeyLogin }: Props) {
  const [rememberedUsername] = useState(readLastUsername)
  const [username, setUsername] = useState(rememberedUsername)
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [passkeyLoading, setPasskeyLoading] = useState(false)
  // null while the capability check is still in flight, so the primary button
  // is not swapped out underneath someone who is already reading the form.
  const [platformAvailable, setPlatformAvailable] = useState<boolean | null>(null)
  const [passwordMode, setPasswordMode] = useState(false)
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
      // No Touch ID on this machine => the password is the only way in, so
      // open that path immediately rather than hiding it behind a button.
      if (!available) setPasswordMode(true)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const touchIdReady = platformAvailable === true
  const busy = loading || passkeyLoading
  const mode: Mode = passwordMode ? 'password' : 'touch'
  const isRemembered = rememberedUsername !== '' && username === rememberedUsername

  async function handlePasswordLogin() {
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
      await onPasskeyLogin()
    } catch (err) {
      const message = describePasskeyError(err, 'Touch ID sign-in failed')
      // Nothing is enrolled yet (first run, or every passkey was removed), so
      // Touch ID cannot work at all. Open the password path and say why.
      if (/no passkey is enrolled/i.test(message)) {
        setPasswordMode(true)
        setError('No passkey registered yet — sign in with your password, then register this Mac.')
      } else {
        setError(message)
      }
    } finally {
      setPasskeyLoading(false)
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    void (passwordMode ? handlePasswordLogin() : handlePasskeyLogin())
  }

  const fieldClass =
    'h-9 box-border w-full border border-outline-variant bg-surface-container-low px-2.5 text-sm text-on-surface focus:border-primary focus:bg-white focus:outline-none'
  const buttonClass =
    'h-[38px] w-full border border-primary label-caps text-[11px] font-bold disabled:opacity-50'
  const marketOpen = marketStatusNow() === 'OPEN'

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
          <StationPanel clock={istClock(now)} mode={mode} platformAvailable={platformAvailable} />

          <form onSubmit={handleSubmit} className="flex flex-col gap-4 p-7">
            <div className="flex items-start justify-between gap-3">
              <div className="flex flex-col gap-1">
                <h2 className="m-0 text-lg font-bold tracking-[-0.01em]">Sign in</h2>
                <p className="m-0 text-xs text-on-surface-variant">
                  {passwordMode ? 'Use your owner credentials.' : 'Touch the sensor to continue.'}
                </p>
              </div>
              <span className="label-caps whitespace-nowrap border border-outline-variant bg-surface-container px-2 py-1 text-on-surface-variant">
                {passwordMode ? 'Password + TOTP' : 'Passkey'}
              </span>
            </div>

            {error && (
              <div className="flex items-start gap-2 border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
                <span className="material-symbols-outlined text-base leading-none">error</span>
                <span>{error}</span>
              </div>
            )}

            {!passwordMode && (
              <button
                type="submit"
                disabled={busy || !touchIdReady}
                className={`flex min-h-[170px] flex-1 flex-col items-center justify-center gap-3.5 border font-sans text-on-surface disabled:opacity-50 ${
                  passkeyLoading
                    ? 'border-primary bg-surface-container-low'
                    : 'border-outline-variant bg-white hover:bg-surface-container-low'
                }`}
              >
                <span
                  className={`flex size-[72px] items-center justify-center rounded-full border bg-white ${
                    passkeyLoading ? 'border-primary nr-ring' : 'border-outline-variant'
                  }`}
                >
                  <span
                    className="material-symbols-outlined text-primary"
                    style={{ fontSize: '44px', lineHeight: '44px' }}
                  >
                    fingerprint
                  </span>
                </span>
                <span className="flex flex-col items-center gap-1">
                  <span className="text-[13px] font-bold">
                    {passkeyLoading ? 'Waiting for Touch ID…' : 'Sign in with Touch ID'}
                  </span>
                  <span className="text-[11px] text-on-surface-variant">
                    {passkeyLoading ? 'Rest your finger on the sensor' : 'Click, then touch the sensor'}
                  </span>
                </span>
              </button>
            )}

            {passwordMode && (
              <>
                <label className="flex flex-col gap-1.5">
                  <span className="flex justify-between">
                    <span className="label-caps text-on-surface-variant">Username</span>
                    {isRemembered && <span className="label-caps text-slate-400">Remembered</span>}
                  </span>
                  <input
                    className={fieldClass}
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    autoComplete="username"
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
                  />
                </label>

                <button
                  type="submit"
                  disabled={busy || !username.trim() || !password}
                  className={`${buttonClass} bg-primary text-white hover:opacity-90`}
                >
                  {loading ? 'Signing in…' : 'Sign in'}
                </button>
              </>
            )}

            {touchIdReady && (
              <div className="flex flex-col gap-4">
                <div className="flex items-center gap-2.5">
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
                    setPassword('')
                    setPasswordMode((value) => !value)
                  }}
                  className={`${buttonClass} flex items-center justify-center gap-2 bg-white text-primary hover:bg-sky-50`}
                >
                  <span className="material-symbols-outlined text-[18px] leading-none tracking-normal normal-case">
                    {passwordMode ? 'fingerprint' : 'password'}
                  </span>
                  {passwordMode ? 'Use Touch ID instead' : 'Use password instead'}
                </button>
              </div>
            )}

            <p className="m-0 text-[11px] leading-4 text-on-surface-variant text-pretty">
              {passwordMode
                ? touchIdReady
                  ? 'The password path also needs your authenticator code, which lives on your phone.'
                  : "Touch ID isn't available on this device, so password and authenticator code are the way in."
                : 'Your fingerprint is the whole sign-in — no password, no code from your phone.'}
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

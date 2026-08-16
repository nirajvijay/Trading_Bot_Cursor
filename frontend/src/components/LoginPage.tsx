import { useState, type FormEvent } from 'react'

interface Props {
  onLogin: (username: string, password: string, totp?: string) => Promise<void>
}

export function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    setError(null)
    try {
      await onLogin(username.trim(), password, totp.trim() || undefined)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center bg-background px-4">
      <form
        onSubmit={(e) => void handleSubmit(e)}
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
            autoComplete="username"
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
        <label className="block space-y-1">
          <span className="label-caps text-on-surface-variant">MFA code (if enabled)</span>
          <input
            className="w-full border border-outline-variant bg-surface-container-low px-2 py-1.5 text-sm font-data"
            value={totp}
            onChange={(e) => setTotp(e.target.value)}
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="Optional unless MFA required"
          />
        </label>
        <button
          type="submit"
          disabled={loading}
          className="w-full bg-primary text-white py-2 label-caps font-bold hover:opacity-90 disabled:opacity-50"
        >
          {loading ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}

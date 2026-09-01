import { useEffect, useState, type FormEvent } from 'react'
import { postStepUp } from '../../api/client'

interface Props {
  open: boolean
  title: string
  onClose: () => void
  onSuccess: () => void
}

export function AdminStepUpModal({ open, title, onClose, onSuccess }: Props) {
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open) {
      setPassword('')
      setTotp('')
      setError(null)
      setLoading(false)
    }
  }, [open])

  if (!open) return null

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      await postStepUp(password, totp || undefined)
      onSuccess()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Step-up failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4">
      <div className="bg-surface border border-outline-variant rounded-lg shadow-lg w-full max-w-md p-5">
        <h2 className="text-base font-semibold text-on-surface mb-1">{title}</h2>
        <p className="text-xs text-on-surface-variant mb-4">
          Confirm your password (and MFA code if enabled) to continue.
        </p>
        <form onSubmit={(e) => void handleSubmit(e)} className="space-y-3">
          <label className="block text-xs font-medium text-on-surface">
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full border border-outline-variant rounded px-2 py-1.5 text-sm"
              required
              autoComplete="current-password"
            />
          </label>
          <label className="block text-xs font-medium text-on-surface">
            MFA code (if enabled)
            <input
              type="text"
              inputMode="numeric"
              value={totp}
              onChange={(e) => setTotp(e.target.value)}
              className="mt-1 w-full border border-outline-variant rounded px-2 py-1.5 text-sm"
              autoComplete="one-time-code"
            />
          </label>
          {error && <p className="text-xs text-red-700">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-xs border border-outline-variant rounded"
              disabled={loading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-3 py-1.5 text-xs bg-primary text-white rounded disabled:opacity-50"
              disabled={loading}
            >
              {loading ? 'Verifying…' : 'Confirm'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

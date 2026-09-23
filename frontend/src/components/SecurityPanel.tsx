import { useCallback, useEffect, useState } from 'react'
import {
  fetchPasskeys,
  postDeletePasskey,
  postPasskeyRegisterOptions,
  postPasskeyRegisterVerify,
} from '../api/client'
import type { PasskeyInfo } from '../api/types'
import {
  createPasskey,
  describePasskeyError,
  isPlatformAuthenticatorAvailable,
} from '../lib/passkey'

interface Props {
  onClose: () => void
  onPasskeyCountChange?: (count: number) => void
}

function formatStamp(value: string | null): string {
  if (!value) return 'never'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('en-GB', { timeZone: 'Asia/Kolkata' })
}

export function SecurityPanel({ onClose, onPasskeyCountChange }: Props) {
  const [passkeys, setPasskeys] = useState<PasskeyInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [platformAvailable, setPlatformAvailable] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const [password, setPassword] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await fetchPasskeys()
      setPasskeys(data.passkeys)
      onPasskeyCountChange?.(data.passkeys.length)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load passkeys')
    }
  }, [onPasskeyCountChange])

  useEffect(() => {
    void load()
    void isPlatformAuthenticatorAvailable().then(setPlatformAvailable)
  }, [load])

  async function handleRegister() {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const { challenge_id, options } = await postPasskeyRegisterOptions()
      const credential = await createPasskey(options)
      await postPasskeyRegisterVerify(challenge_id, credential)
      setNotice('This Mac is now registered.')
      await load()
    } catch (err) {
      setError(describePasskeyError(err, 'Touch ID setup failed'))
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete() {
    if (!pendingDelete) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await postDeletePasskey(pendingDelete, password)
      setPendingDelete(null)
      setPassword('')
      setNotice('Passkey removed.')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove passkey')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
      <div className="w-full max-w-lg space-y-4 border border-outline-variant bg-white px-5 py-6 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-on-surface">Sign-in security</h2>
            <p className="mt-1 text-xs leading-5 text-on-surface-variant">
              Registered passkeys sign you in with Touch ID instead of an authenticator
              code. Remove a Mac you no longer use — a leftover entry blocks that machine
              from registering again.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="label-caps text-[10px] text-on-surface-variant underline"
          >
            Close
          </button>
        </div>

        {error && (
          <div className="border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">{error}</div>
        )}
        {notice && (
          <div className="border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
            {notice}
          </div>
        )}

        <div className="border border-outline-variant">
          {passkeys === null ? (
            <p className="px-3 py-3 text-xs text-on-surface-variant">Loading…</p>
          ) : passkeys.length === 0 ? (
            <p className="px-3 py-3 text-xs text-on-surface-variant">
              No passkeys registered. You are signing in with an authenticator code.
            </p>
          ) : (
            <ul className="divide-y divide-outline-variant">
              {passkeys.map((key) => (
                <li key={key.credential_id} className="flex items-center justify-between gap-3 px-3 py-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm text-on-surface">{key.name}</p>
                    <p className="font-data text-[10px] text-on-surface-variant">
                      added {formatStamp(key.created_at)} · last used {formatStamp(key.last_used_at)}
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setPendingDelete(key.credential_id)
                      setPassword('')
                      setError(null)
                      setNotice(null)
                    }}
                    className="shrink-0 label-caps text-[10px] text-negative underline disabled:opacity-50"
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {pendingDelete && (
          <div className="space-y-2 border border-outline-variant bg-surface-container px-3 py-3">
            <p className="text-xs text-on-surface-variant">
              Confirm your password to remove this passkey.
            </p>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className="w-full border border-outline-variant bg-white px-2 py-1.5 text-sm"
              autoFocus
            />
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy || !password}
                onClick={() => void handleDelete()}
                className="flex-1 bg-negative py-2 label-caps text-[10px] font-bold text-white disabled:opacity-50"
              >
                {busy ? 'Removing…' : 'Remove passkey'}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setPendingDelete(null)
                  setPassword('')
                }}
                className="flex-1 border border-outline-variant py-2 label-caps text-[10px] disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        <button
          type="button"
          disabled={busy || !platformAvailable}
          onClick={() => void handleRegister()}
          className="w-full bg-primary py-2 label-caps font-bold text-white hover:opacity-90 disabled:opacity-50"
        >
          {busy ? 'Waiting for Touch ID…' : 'Register this Mac'}
        </button>
        {!platformAvailable && (
          <p className="text-[10px] leading-4 text-on-surface-variant">
            This machine has no built-in Touch ID authenticator, so it cannot be registered.
          </p>
        )}
      </div>
    </div>
  )
}

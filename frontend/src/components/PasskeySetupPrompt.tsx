import { useState } from 'react'
import { postPasskeyRegisterOptions, postPasskeyRegisterVerify } from '../api/client'
import { createPasskey } from '../lib/passkey'

interface Props {
  onDone: () => void
}

export function PasskeySetupPrompt({ onDone }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSetup() {
    setBusy(true)
    setError(null)
    try {
      const { challenge_id, options } = await postPasskeyRegisterOptions()
      const credential = await createPasskey(options)
      await postPasskeyRegisterVerify(challenge_id, credential)
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Touch ID setup failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
      <div className="w-full max-w-md space-y-4 border border-outline-variant bg-white px-5 py-6 shadow-xl">
        <div>
          <h2 className="text-lg font-bold text-on-surface">Set up Mac Touch ID</h2>
          <p className="mt-1 text-xs leading-5 text-on-surface-variant">
            Register this Mac as a passkey. After setup, you can sign in with your username,
            password, and fingerprint instead of opening Google Authenticator.
          </p>
        </div>
        {error && <div className="border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">{error}</div>}
        <button
          type="button"
          disabled={busy}
          onClick={() => void handleSetup()}
          className="w-full bg-primary py-2 label-caps font-bold text-white hover:opacity-90 disabled:opacity-50"
        >
          {busy ? 'Waiting for Touch ID...' : 'Register this Mac'}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onDone}
          className="w-full label-caps text-[10px] text-on-surface-variant underline disabled:opacity-50"
        >
          Do this later
        </button>
      </div>
    </div>
  )
}

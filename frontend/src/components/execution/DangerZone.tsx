import { useState } from 'react'
import type { ExecutionStatus } from '../../api/types'

const KILL_PHRASE = 'KILL'

/** Session controls, set apart on purpose: everything here changes what the
 *  engine will do with real money, and one of them ends the day. */
export function DangerZone({
  status,
  busy,
  onStopEntries,
  onStartEntries,
  onKillAll,
}: {
  status: ExecutionStatus
  busy: string | null
  onStopEntries: () => void
  onStartEntries: () => void
  onKillAll: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [typed, setTyped] = useState('')

  // The same window rule as the initial start, applied to every click: after
  // 14:00 entries cannot be switched back on, though the loop keeps running.
  const startBlocked = !status.entries_allowed && !status.entries_stopped
  const entriesOn = !status.entries_stopped && status.entries_allowed

  return (
    <section className="border border-red-200 rounded-md bg-[#fffafa] px-3 py-2.5 flex flex-col gap-2">
      <h2 className="label-caps text-negative">Session controls</h2>
      <div className="grid grid-cols-2 gap-2">
        {entriesOn ? (
          <button
            type="button"
            disabled={busy === 'stop'}
            onClick={onStopEntries}
            className="label-caps px-2.5 py-2 rounded border border-outline-variant bg-surface hover:border-[#c4c7cf] whitespace-nowrap"
          >
            {busy === 'stop' ? 'Stopping entries…' : 'Stop new entries'}
          </button>
        ) : (
          <button
            type="button"
            disabled={busy === 'start' || startBlocked}
            onClick={onStartEntries}
            className="label-caps px-2.5 py-2 rounded border border-outline-variant bg-surface hover:border-[#c4c7cf] whitespace-nowrap disabled:text-on-surface-variant disabled:bg-surface-container"
            title={startBlocked ? 'Past the 14:00 cutoff' : undefined}
          >
            {busy === 'start' ? 'Resuming…' : 'Resume new entries'}
          </button>
        )}
        <button
          type="button"
          disabled={busy === 'kill_all'}
          onClick={() => {
            setTyped('')
            setConfirming(true)
          }}
          className="label-caps px-2.5 py-2 rounded bg-negative text-white hover:opacity-90 whitespace-nowrap"
        >
          {busy === 'kill_all' ? 'Killing…' : 'Kill it all now'}
        </button>
      </div>
      <p className="text-[10px] leading-snug text-on-surface-variant text-pretty">
        {entriesOn
          ? 'Stopping entries does not stop the engine — it keeps watching, reconciling and protecting open positions.'
          : startBlocked
            ? 'Entries cannot be resumed after 14:00. The engine keeps managing open positions.'
            : 'Entries are off. The engine is still running and still reconciling.'}
      </p>

      {confirming && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-surface border-2 border-negative rounded-md max-w-md w-full shadow-xl">
            <h3 className="px-4 py-3 border-b border-negative text-[13px] font-extrabold uppercase tracking-tight text-negative">
              Kill it all now
            </h3>
            <div className="px-4 py-3 space-y-2 text-[12px]">
              <p>This will, in order:</p>
              <ol className="list-decimal pl-5 space-y-1 text-on-surface-variant">
                <li>Cancel every live stop, then market out of every open position.</li>
                <li>Keep watching until every position is confirmed closed.</li>
                <li>Shut the engine down. The session is over for the day.</li>
              </ol>
              <p className="font-semibold">
                {status.open_positions} open position
                {status.open_positions === 1 ? '' : 's'} will be closed at market.
              </p>
              <label className="block pt-1">
                <span className="text-on-surface-variant">
                  Type <span className="font-data font-bold">{KILL_PHRASE}</span> to
                  confirm
                </span>
                <input
                  autoFocus
                  className="mt-1 w-full font-data bg-surface-container-low border border-outline-variant rounded px-2 py-1.5 focus:outline-none focus:border-negative"
                  value={typed}
                  onChange={(e) => setTyped(e.target.value.toUpperCase())}
                />
              </label>
            </div>
            <div className="px-4 py-3 border-t border-outline-variant flex justify-end gap-2">
              <button
                type="button"
                className="label-caps px-3 py-2 rounded border border-outline-variant"
                onClick={() => setConfirming(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={typed !== KILL_PHRASE}
                className="label-caps px-3 py-2 rounded bg-negative text-white disabled:bg-surface-container disabled:text-on-surface-variant"
                onClick={() => {
                  setConfirming(false)
                  onKillAll()
                }}
              >
                Close everything and stop
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

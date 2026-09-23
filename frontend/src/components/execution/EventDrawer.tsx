import { useEffect, useState } from 'react'
import { fetchExecutionEvents } from '../../api/client'
import type { ExecutionEvent } from '../../api/types'
import { timeIst } from './format'

const EVENT_LABELS: Record<string, string> = {
  entry_intent: 'Intent written before the broker call',
  entry_submitted: 'Market order sent',
  entry_ambiguous: 'Broker response was ambiguous',
  entry_unreached: 'Order never reached the broker',
  entry_filled: 'Filled',
  entry_rejected: 'Broker rejected the entry',
  protected: 'Protective stop live',
  stop_accepted_visibility_unknown: 'Stop accepted, not yet visible',
  stop_place_failed: 'Stop placement failed',
  stop_missing_at_broker: 'Stop vanished at the broker',
  stop_cancelled: 'Stop cancelled',
  stop_cancel_ambiguous: 'Stop cancellation unconfirmed',
  reconciled: 'Adopted broker truth',
  exit_submitted: 'Exit order sent',
  flatten_failed: 'Flatten failed',
  closed: 'Closed',
  cancelled: 'Cancelled',
  skipped: 'Skipped',
  entry_cancel_raced_fill: 'Cancel raced a fill',
}

function isAlarming(type: string): boolean {
  return (
    type.includes('failed') ||
    type.includes('ambiguous') ||
    type.includes('rejected') ||
    type === 'stop_missing_at_broker'
  )
}

/** The append-only diary for one trade.
 *
 *  This is the review surface: after a small live trade, pull this up and read
 *  it against what Kite's own order history shows for the same trade. */
export function EventDrawer({
  tradeId,
  onClose,
}: {
  tradeId: string
  onClose: () => void
}) {
  const [events, setEvents] = useState<ExecutionEvent[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setEvents(null)
    setError(null)
    fetchExecutionEvents(tradeId)
      .then((data) => {
        if (!cancelled) setEvents(data.events)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load')
      })
    return () => {
      cancelled = true
    }
  }, [tradeId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <aside
        className="bg-surface w-full max-w-xl h-full flex flex-col border-l border-outline-variant shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="px-4 py-3 border-b border-outline-variant flex items-center gap-3">
          <div className="min-w-0">
            <h2 className="text-[13px] font-extrabold uppercase tracking-tight">
              Event diary
            </h2>
            <p className="font-data text-[11px] text-on-surface-variant truncate">
              {tradeId}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto label-caps px-2.5 py-1.5 rounded-sm border border-outline-variant hover:bg-surface-container-low"
          >
            Close
          </button>
        </header>

        <div className="flex-1 overflow-y-auto custom-scrollbar px-4 py-3">
          {error && (
            <p className="px-3 py-2 bg-red-50 border border-red-200 rounded-sm text-[12px] text-negative">
              {error}
            </p>
          )}
          {!error && events === null && (
            <p className="text-[12px] text-on-surface-variant">Loading…</p>
          )}
          {events?.length === 0 && (
            <p className="text-[12px] text-on-surface-variant">
              No events recorded for this trade.
            </p>
          )}
          <ol className="space-y-2">
            {events?.map((event) => (
              <li
                key={event.event_id}
                className={`border rounded-sm px-3 py-2 ${
                  isAlarming(event.event_type)
                    ? 'border-red-200 bg-red-50'
                    : 'border-outline-variant bg-surface-container-low'
                }`}
              >
                <div className="flex items-baseline gap-2">
                  <span className="font-data text-[11px] tabular-nums text-on-surface-variant">
                    {timeIst(event.at)}
                  </span>
                  <span
                    className={`text-[12px] font-semibold ${
                      isAlarming(event.event_type) ? 'text-negative' : ''
                    }`}
                  >
                    {EVENT_LABELS[event.event_type] ?? event.event_type}
                  </span>
                  <span className="ml-auto font-data text-[10px] text-on-surface-variant">
                    {event.event_type}
                  </span>
                </div>
                {Object.keys(event.payload).length > 0 && (
                  <dl className="mt-1.5 grid gap-x-4 gap-y-0.5 sm:grid-cols-2">
                    {Object.entries(event.payload).map(([key, value]) => {
                      const text =
                        value === null || value === undefined
                          ? '—'
                          : typeof value === 'object'
                            ? JSON.stringify(value)
                            : String(value)
                      // A long value (a broker's rejection message) gets the
                      // full width and wraps. Truncating it would hide the one
                      // thing worth reading.
                      const wide = text.length > 24
                      return (
                        <div
                          key={key}
                          className={`flex gap-2 justify-between items-baseline ${
                            wide ? 'sm:col-span-2' : ''
                          }`}
                        >
                          <dt className="text-[10px] text-on-surface-variant whitespace-nowrap">
                            {key}
                          </dt>
                          <dd
                            className={`font-data text-[10px] tabular-nums text-right ${
                              wide ? 'break-words text-left' : ''
                            }`}
                          >
                            {text}
                          </dd>
                        </div>
                      )
                    })}
                  </dl>
                )}
              </li>
            ))}
          </ol>
        </div>

        <footer className="px-4 py-2.5 border-t border-outline-variant">
          <p className="text-[10px] text-on-surface-variant">
            Append-only. Every line was written when it happened and is never rewritten —
            read it against Kite's own order history for this trade.
          </p>
        </footer>
      </aside>
    </div>
  )
}

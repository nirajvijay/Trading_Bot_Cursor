import type { LivePnlFeedState } from '../../api/types'
import { reasonLabel } from './format'

/**
 * Where the ongoing (open) P&L is coming from right now.
 *
 * LIVE: marked from the engine's own Kite WebSocket, tick by tick.
 * Fallback: the feed is down or stale, so the number is Kite's REST pnl,
 * which only refreshes on Kite's own slower cadence. PAPER shows nothing.
 */
export function LivePnlBadge({
  state,
  reason,
}: {
  state: LivePnlFeedState | null | undefined
  reason?: string | null
}) {
  if (state === 'live') {
    return (
      <span
        title="Ongoing P&L is live from the Kite WebSocket"
        className="label-caps px-1.5 py-0.5 rounded-sm border text-positive border-emerald-200 bg-emerald-50"
      >
        Live
      </span>
    )
  }
  if (state === 'fallback') {
    const why = reason ? reasonLabel(reason.split(':')[0]) : null
    return (
      <span
        role="status"
        title={`WebSocket feed is stale or down${why ? ` (${why})` : ''}. Ongoing P&L falls back to Kite REST pnl, which is not live and is per stock for the day.${
          // Only the raw text when it adds detail, e.g. "ws_start_failed: <error>".
          reason && reason.includes(':') ? `\n${reason}` : ''
        }`}
        className="label-caps px-1.5 py-0.5 rounded-sm border text-amber-800 border-amber-300 bg-amber-50 whitespace-nowrap"
      >
        Fallback: Kite REST · feed stale
      </span>
    )
  }
  return null
}

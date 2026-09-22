import { useEffect, useState } from 'react'
import { fetchVwapHealth } from '../api/client'
import type { VwapHealthStatus } from '../api/types'

const POLL_MS = 30_000

const STATUS_COPY: Record<VwapHealthStatus['status'], { label: string; className: string }> = {
  ok: { label: 'HEALTHY', className: 'bg-emerald-50 text-positive border-emerald-200' },
  alarm: { label: 'ALARM', className: 'bg-red-50 text-negative border-red-300' },
  unknown: { label: 'NOT CHECKED YET', className: 'bg-surface-container text-on-surface-variant border-outline-variant' },
}

function Stat({ label, value, warn }: { label: string; value: number | string; warn?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5 px-4 py-3 border border-outline-variant bg-white">
      <span className="label-caps text-[10px] text-on-surface-variant">{label}</span>
      <span className={`font-data text-lg ${warn ? 'text-negative font-bold' : 'text-on-surface'}`}>{value}</span>
    </div>
  )
}

export function VwapHealthPage() {
  const [health, setHealth] = useState<VwapHealthStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const result = await fetchVwapHealth()
        if (!cancelled) {
          setHealth(result)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load VWAP health')
        }
      }
    }
    void load()
    const id = window.setInterval(() => void load(), POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  const copy = STATUS_COPY[health?.status ?? 'unknown']

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-auto bg-surface-container-lowest">
      <div className="px-4 py-3 border-b border-outline-variant bg-white shrink-0">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-sm font-extrabold uppercase tracking-tight text-on-surface">
            VWAP Health
          </h1>
          <span className={`label-caps px-2 py-0.5 rounded border text-[10px] ${copy.className}`}>
            {copy.label}
          </span>
          {health?.session_date && (
            <span className="label-caps text-[10px] text-on-surface-variant">
              session {health.session_date}
            </span>
          )}
          {health?.checked_at && (
            <span className="label-caps text-[10px] text-on-surface-variant ml-auto">
              last checked {health.checked_at}
            </span>
          )}
        </div>
        <p className="mt-1 text-[11px] text-on-surface-variant">
          Compares real triggers against saved VWAP verdicts so a silent classify/persist failure
          (like the one that ran unnoticed Sep 2–22, 2026) shows up the same day instead of weeks later.
        </p>
      </div>

      <div className="p-4 flex flex-col gap-3">
        {error && (
          <div className="px-3 py-2 bg-red-50 border border-red-200 text-red-800 text-sm">{error}</div>
        )}

        {health?.status === 'unknown' && !error && (
          <div className="px-3 py-2 bg-amber-50 border border-amber-200 text-amber-800 text-sm">
            The standalone health-check job hasn't written a result yet. It runs on its own
            schedule (every 10-15 min during market hours) — this isn't the same as the trading
            engine being down.
          </div>
        )}

        {health?.reason && (
          <div className="px-3 py-2 bg-red-50 border border-red-200 text-red-800 text-sm">
            {health.reason}
          </div>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
          <Stat label="Triggered" value={health?.triggered_count ?? '-'} />
          <Stat label="Qualified" value={health?.qualified_count ?? '-'} />
          <Stat label="Stuck (no verdict)" value={health?.stuck_count ?? '-'} warn={(health?.stuck_count ?? 0) > 0} />
          <Stat
            label="Classify failures"
            value={health?.callback_failures ?? '-'}
            warn={(health?.callback_failures ?? 0) > 0}
          />
          <Stat
            label="Persist failures"
            value={health?.persist_failures ?? '-'}
            warn={(health?.persist_failures ?? 0) > 0}
          />
        </div>
      </div>
    </div>
  )
}

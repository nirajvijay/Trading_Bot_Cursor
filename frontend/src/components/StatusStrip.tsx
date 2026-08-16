import type { ObservationReadiness, RunnerStatus, SessionCoverage } from '../api/types'
import { resolveFeedStatus, type RunnerPresence } from '../lib/feedStatus'
import { formatTimeIst } from '../lib/format'
import { FeedStatusBadge } from './FeedStatusBadge'

interface Props {
  coverage: SessionCoverage | null
  status: RunnerStatus | null
  runnerPresence: RunnerPresence
  observationReadiness: ObservationReadiness | null
  startingObservation: boolean
  observationError: string | null
  onStartObservation: () => void
  onExport: () => void
}

export function StatusStrip({
  coverage,
  status,
  runnerPresence,
  observationReadiness,
  startingObservation,
  observationError,
  onStartObservation,
  onExport,
}: Props) {
  const runnerRunning = runnerPresence === 'running'
  const canStart = observationReadiness?.can_start ?? false
  const disabledReason = observationReadiness?.reason ?? 'Loading readiness…'
  const feed = resolveFeedStatus(status, runnerPresence)

  return (
    <div className="bg-surface-container-low px-4 py-2 flex justify-between items-center border-b border-outline-variant shrink-0 gap-4 flex-wrap">
      <div className="flex gap-4 flex-wrap items-center text-sm">
        <FeedStatusBadge feed={feed} />
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="label-caps text-on-surface-variant">Successful:</span>
            <span className="font-data text-xs text-positive">{coverage?.continuation_successful ?? 0}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="label-caps text-on-surface-variant">Failed:</span>
            <span className="font-data text-xs text-negative">{coverage?.continuation_failed ?? 0}</span>
          </div>
        </div>
        {status?.last_tick_time && runnerRunning && (
          <div className="flex items-center gap-1.5">
            <span className="label-caps text-on-surface-variant">Last tick:</span>
            <span className="font-data text-xs">{formatTimeIst(status.last_tick_time)} IST</span>
          </div>
        )}
        <div className="flex items-center gap-1.5 hidden md:flex">
          <span className="label-caps text-on-surface-variant">Session:</span>
          <span className="font-data text-xs">{coverage?.session_date ?? '-'}</span>
        </div>
        <div className="flex items-center gap-1.5 hidden lg:flex">
          <span className="label-caps text-on-surface-variant">1m data:</span>
          <span className="font-data text-xs">
            {coverage ? `${coverage.tokens_with_1m}/${coverage.subscribed}` : '-'}
          </span>
        </div>
        {observationError && (
          <span className="text-xs text-negative">{observationError}</span>
        )}
      </div>
      <div className="flex items-center gap-2">
        {runnerRunning ? (
          <span
            className={`label-caps font-extrabold px-3 py-1 border rounded flex items-center gap-1.5 ${
              feed.tone === 'ok'
                ? 'bg-emerald-50 text-positive border-emerald-200'
                : feed.tone === 'warn'
                  ? 'bg-amber-50 text-amber-800 border-amber-300'
                  : feed.tone === 'error'
                    ? 'bg-red-50 text-negative border-red-200'
                    : 'bg-surface-container text-on-surface-variant border-outline-variant'
            }`}
            title={
              observationReadiness?.expected_stop_at
                ? `Stops at ${formatTimeIst(observationReadiness.expected_stop_at)} IST`
                : 'Runs until 15:30 IST'
            }
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                feed.tone === 'ok' ? 'bg-positive pulse-green' : feed.tone === 'warn' ? 'bg-amber-500' : feed.tone === 'error' ? 'bg-negative' : 'bg-on-surface-variant'
              }`}
            />
            {feed.code === 'STALE' ? 'Observation Running — Feed Stale' : 'Observation Running'}
            <span className="font-normal text-[10px] opacity-80">· Until 15:30 IST</span>
          </span>
        ) : (
          <button
            type="button"
            className="bg-primary text-white px-3 py-1 rounded shadow-sm hover:opacity-90 label-caps flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={!canStart || startingObservation || runnerPresence === 'unknown'}
            title={
              runnerPresence === 'unknown'
                ? 'Status unavailable — cannot confirm runner state'
                : !canStart
                  ? disabledReason
                  : 'Start live observation until 15:30 IST'
            }
            onClick={onStartObservation}
          >
            <span className="material-symbols-outlined text-[16px]">
              {startingObservation ? 'hourglass_top' : 'play_arrow'}
            </span>
            {startingObservation ? 'Starting…' : 'Start Observation'}
          </button>
        )}
        <button
          type="button"
          className="border border-outline-variant bg-white px-3 py-1 rounded label-caps text-on-surface-variant flex items-center gap-1.5 hover:bg-surface-container"
          disabled
          title="Filter (coming soon)"
        >
          <span className="material-symbols-outlined text-[16px]">filter_list</span>
          Filter
        </button>
        <button
          type="button"
          className="border border-outline-variant bg-white px-3 py-1 rounded label-caps text-on-surface-variant flex items-center gap-1.5 hover:bg-surface-container"
          onClick={onExport}
        >
          <span className="material-symbols-outlined text-[16px]">download</span>
          Export CSV
        </button>
      </div>
    </div>
  )
}

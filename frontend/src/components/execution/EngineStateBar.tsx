import type { ExecutionStatus } from '../../api/types'
import { ageLabel, reasonLabel } from './format'

/** What the engine is doing, in one glance, with the crash case unmissable. */
function stateView(status: ExecutionStatus | null) {
  if (!status || status.engine_state === 'absent') {
    return {
      label: 'NOT RUNNING',
      tone: 'idle' as const,
      detail: 'No engine has been started today.',
    }
  }
  if (status.engine_state === 'crashed') {
    return {
      label: 'CRASHED',
      tone: 'alarm' as const,
      detail:
        'The heartbeat went silent with no stop note. The process died unexpectedly — check before restarting.',
    }
  }
  if (status.engine_state === 'stopped') {
    return {
      label: 'STOPPED',
      tone: 'idle' as const,
      detail: `Finished on purpose: ${reasonLabel(status.stop_reason)}.`,
    }
  }
  if (status.entries_paused) {
    return {
      label: 'RUNNING · ENTRIES PAUSED',
      tone: 'warn' as const,
      detail: `Managing open positions. Entries held: ${reasonLabel(status.pause_reason)}.`,
    }
  }
  if (status.entries_stopped) {
    return {
      label: 'RUNNING · ENTRIES OFF',
      tone: 'warn' as const,
      detail: 'Watching and reconciling. New entries are switched off.',
    }
  }
  if (!status.entries_allowed) {
    return {
      label: 'RUNNING · NO NEW ENTRIES',
      tone: 'warn' as const,
      detail: 'Past the 14:00 cutoff. Open positions are still managed.',
    }
  }
  return {
    label: 'RUNNING · TAKING TRADES',
    tone: 'live' as const,
    detail: 'Armed. The next qualifying trigger will be entered for real.',
  }
}

const TONES = {
  live: 'bg-emerald-50 text-positive border-emerald-200',
  warn: 'bg-amber-50 text-amber-800 border-amber-200',
  alarm: 'bg-red-100 text-negative border-negative',
  idle: 'bg-surface-container text-on-surface-variant border-outline-variant',
} as const

export function EngineStateBar({
  status,
  sessionDate,
}: {
  status: ExecutionStatus | null
  sessionDate: string
}) {
  const view = stateView(status)
  const crashed = status?.engine_state === 'crashed'

  return (
    <section className="bg-surface border border-outline-variant rounded-sm">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
        <div className="flex items-center gap-3 min-w-0">
          <span
            className={`label-caps px-2.5 py-1 border rounded-sm whitespace-nowrap ${
              TONES[view.tone]
            } ${crashed ? 'animate-pulse' : ''}`}
          >
            {view.label}
          </span>
          <p className="text-[12px] text-on-surface-variant min-w-0">{view.detail}</p>
        </div>

        <dl className="ml-auto flex items-center gap-4 font-data text-[11px] text-on-surface-variant">
          {status?.is_live && (
            <span className="label-caps px-2 py-0.5 border border-negative text-negative rounded-sm">
              LIVE ORDERS
            </span>
          )}
          <div>
            <dt className="label-caps">Tick</dt>
            <dd className="tabular-nums">
              {status ? status.tick_count.toLocaleString('en-IN') : '—'}
            </dd>
          </div>
          <div>
            <dt className="label-caps">Heartbeat</dt>
            <dd className="tabular-nums">{ageLabel(status?.heartbeat_age_seconds)}</dd>
          </div>
          <div>
            <dt className="label-caps">Session</dt>
            <dd className="tabular-nums">{status?.session_date ?? sessionDate}</dd>
          </div>
          {status?.run_id && (
            <div>
              <dt className="label-caps">Run</dt>
              <dd className="tabular-nums">{status.run_id}</dd>
            </div>
          )}
        </dl>
      </div>

      {status && Object.keys(status.escalations).length > 0 && (
        <div className="px-4 py-2 border-t border-negative bg-red-50 text-[12px] text-negative">
          <p className="label-caps mb-1">Repeated step failures</p>
          <ul className="space-y-0.5 font-data text-[11px]">
            {Object.entries(status.escalations).map(([step, detail]) => (
              <li key={step}>
                <span className="font-semibold">{step}</span> — {detail}
              </li>
            ))}
          </ul>
        </div>
      )}

      {status?.last_error && Object.keys(status.escalations).length === 0 && (
        <p className="px-4 py-2 border-t border-outline-variant bg-amber-50 text-[11px] font-data text-amber-900">
          Last error · {status.last_error}
        </p>
      )}
    </section>
  )
}

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

function Fact({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div
      className={`px-4 py-3 border-r border-outline-variant ${wide ? 'min-w-[110px]' : 'min-w-[96px]'}`}
    >
      <dt className="label-caps text-on-surface-variant">{label}</dt>
      <dd className="mt-[3px] font-data text-[12px] tabular-nums">{value}</dd>
    </div>
  )
}

export function EngineStateBar({
  status,
  sessionDate,
}: {
  status: ExecutionStatus | null
  sessionDate: string
}) {
  const view = stateView(status)
  const crashed = status?.engine_state === 'crashed'
  const escalations = status ? Object.entries(status.escalations) : []

  return (
    <section className="bg-surface border border-outline-variant rounded-md overflow-hidden">
      <div className="flex flex-wrap items-stretch">
        <div className="flex-[1_1_440px] flex items-center gap-3.5 px-4 py-3.5 min-w-0">
          <span
            className={`label-caps px-[11px] py-[7px] border rounded whitespace-nowrap ${
              TONES[view.tone]
            } ${crashed ? 'animate-pulse' : ''}`}
          >
            {view.label}
          </span>
          <p className="text-[12px] text-on-surface-variant min-w-0 text-pretty">{view.detail}</p>
        </div>

        <dl className="m-0 flex flex-wrap border-l border-outline-variant">
          {status?.is_live && (
            <div className="px-4 py-3 flex items-center border-r border-outline-variant">
              <span className="label-caps px-2 py-[3px] border border-negative text-negative rounded">
                LIVE ORDERS
              </span>
            </div>
          )}
          <Fact
            label="Tick"
            value={status ? status.tick_count.toLocaleString('en-IN') : '—'}
          />
          <Fact label="Heartbeat" value={ageLabel(status?.heartbeat_age_seconds)} />
          <Fact label="Session" value={status?.session_date ?? sessionDate} wide />
          {status?.run_id && <Fact label="Run" value={status.run_id} wide />}
        </dl>
      </div>

      {escalations.length > 0 && (
        <div className="border-t border-red-200 bg-red-50 px-4 py-2.5 flex flex-wrap items-start gap-x-4 gap-y-2">
          <div className="flex items-center gap-2 shrink-0 pt-[3px]">
            <span className="size-[18px] rounded-full bg-negative text-white flex items-center justify-center text-[11px] font-extrabold">
              !
            </span>
            <span className="label-caps text-negative">Repeated step failures</span>
            <span className="font-data text-[10px] font-bold text-white bg-negative rounded-lg px-1.5 py-px">
              {escalations.length}
            </span>
          </div>
          <ul className="flex-[1_1_420px] min-w-0 flex flex-col gap-1">
            {escalations.map(([step, detail]) => (
              <li
                key={step}
                className="flex items-center gap-2.5 bg-surface border border-red-200 rounded px-2.5 py-[5px]"
              >
                <span className="font-data text-[11px] font-bold text-negative min-w-[120px]">
                  {step}
                </span>
                <span className="text-[12px] min-w-0">{detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {status?.last_error && escalations.length === 0 && (
        <div className="px-4 py-[9px] border-t border-outline-variant bg-amber-50 font-data text-[11px] text-amber-900 flex gap-2.5 items-baseline">
          <span className="font-sans label-caps whitespace-nowrap">Last error</span>
          <span className="min-w-0 break-words [overflow-wrap:anywhere]">{status.last_error}</span>
        </div>
      )}
    </section>
  )
}

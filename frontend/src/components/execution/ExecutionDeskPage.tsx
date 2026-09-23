import { useState } from 'react'
import { useExecutionEngine } from '../../hooks/useExecutionEngine'
import { DangerZone } from './DangerZone'
import { EngineStateBar } from './EngineStateBar'
import { EventDrawer } from './EventDrawer'
import {
  ClosedPositionsTable,
  OpenPositionsTable,
  SkippedPositionsTable,
} from './PositionTables'
import { RiskStrip } from './RiskStrip'
import { StartPanel } from './StartPanel'
import { STALE_MARK_SECONDS, secondsSince } from './format'

/**
 * Execution Desk for the rebuilt engine.
 *
 * A live risk console: state and exposure read at a glance, destructive
 * actions are hard to hit by accident, and anything the engine could not
 * explain is surfaced rather than smoothed over.
 */
export function ExecutionDeskPage({ sessionDate }: { sessionDate: string }) {
  const engine = useExecutionEngine(sessionDate, true)
  const [inspecting, setInspecting] = useState<string | null>(null)

  const status = engine.status
  const positions = engine.positions
  const stopped = !status || status.engine_state !== 'running'
  const markAge = secondsSince(positions?.live_pnl_as_of)
  const markStale = markAge === null || markAge > STALE_MARK_SECONDS

  return (
    <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar bg-background">
      <div className="max-w-[1600px] mx-auto px-4 py-4 flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-[14px] font-extrabold uppercase tracking-tight">
            Execution Desk
          </h1>
          <p className="text-[11px] text-on-surface-variant">
            Entries stop at 14:00 · every position is squared off at 14:50 IST
          </p>
        </div>

        <EngineStateBar status={status} sessionDate={sessionDate} />

        {(engine.error || engine.actionError) && (
          <div className="px-3 py-2 bg-red-50 border border-red-200 rounded-sm text-[12px] text-negative flex items-start gap-3">
            <p className="min-w-0">{engine.actionError || engine.error}</p>
            {engine.actionError && (
              <button
                type="button"
                onClick={engine.clearActionError}
                className="ml-auto label-caps shrink-0"
              >
                Dismiss
              </button>
            )}
          </div>
        )}

        {status && status.unprotected > 0 && (
          <div className="px-3 py-2.5 bg-red-100 border-2 border-negative rounded-sm text-[13px] font-semibold text-negative">
            {status.unprotected} position{status.unprotected === 1 ? '' : 's'} filled with
            no confirmed stop at the broker. The engine retries protection every tick; new
            entries are held back until it succeeds.
          </div>
        )}

        {stopped ? (
          <StartPanel
            preflight={engine.preflight}
            busy={engine.busy === 'start'}
            onStart={engine.start}
          />
        ) : (
          <RiskStrip status={status} />
        )}

        <OpenPositionsTable
          rows={positions?.open ?? []}
          markStale={markStale}
          feedState={positions?.live_pnl_feed_state}
          feedReason={positions?.live_pnl_feed_reason}
          busy={engine.busy}
          onClose={engine.closePosition}
          onInspect={setInspecting}
        />
        <ClosedPositionsTable
          rows={positions?.closed ?? []}
          totalRealised={positions?.total_realised_pnl}
          onInspect={setInspecting}
        />
        <SkippedPositionsTable
          rows={positions?.rejected ?? []}
          onInspect={setInspecting}
        />

        {status && status.engine_state === 'running' && (
          <DangerZone
            status={status}
            busy={engine.busy}
            onStopEntries={engine.stopEntries}
            onStartEntries={engine.startEntries}
            onKillAll={engine.killAll}
          />
        )}

        <p className="text-[10px] text-on-surface-variant pb-2">
          Trailing stops are not part of this engine — a protected position holds its
          structural stop until it is hit, squared off, or closed by hand.
        </p>
      </div>

      {inspecting && (
        <EventDrawer tradeId={inspecting} onClose={() => setInspecting(null)} />
      )}
    </div>
  )
}

import { useCallback, useState } from 'react'
import type { ExecutionPosition } from '../../api/types'
import { useExecutionEngine } from '../../hooks/useExecutionEngine'
import { DangerZone } from './DangerZone'
import { EngineStateBar } from './EngineStateBar'
import { EventDrawer } from './EventDrawer'
import { PositionsCard, type PositionKind } from './PositionTables'
import { PnlSummary, RiskRail } from './RiskStrip'
import { SessionTimeline } from './SessionTimeline'
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
  const [inspecting, setInspecting] = useState<{
    row: ExecutionPosition
    kind: PositionKind
  } | null>(null)
  const closeDrawer = useCallback(() => setInspecting(null), [])

  const status = engine.status
  const positions = engine.positions
  const running = status?.engine_state === 'running'
  const markAge = secondsSince(positions?.live_pnl_as_of)
  const markStale = markAge === null || markAge > STALE_MARK_SECONDS
  const open = positions?.open ?? []

  // Keep the drawer on the freshest copy of its row while the desk polls, and
  // follow it if it moves lists (open → closed). A row that vanished keeps
  // its last snapshot.
  const drawer = (() => {
    if (!inspecting) return null
    const id = inspecting.row.trade_id
    const lists: [PositionKind, ExecutionPosition[]][] = [
      ['open', open],
      ['closed', positions?.closed ?? []],
      ['skipped', positions?.rejected ?? []],
    ]
    for (const [kind, rows] of lists) {
      const row = rows.find((r) => r.trade_id === id)
      if (row) return { row, kind }
    }
    return inspecting
  })()

  return (
    <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar bg-background">
      <div className="max-w-[1600px] mx-auto px-6 pt-5 pb-4 flex flex-col gap-3.5">
        <div className="flex flex-wrap items-end justify-between gap-x-10 gap-y-4">
          <div className="flex flex-col gap-1">
            <h1 className="text-[18px] font-extrabold uppercase tracking-tight">
              Execution Desk
            </h1>
            <p className="text-[12px] text-on-surface-variant">
              Entries stop at 14:00 · every position is squared off at 14:50 IST
            </p>
          </div>
          <SessionTimeline />
        </div>

        <EngineStateBar status={status} sessionDate={sessionDate} />

        {(engine.error || engine.actionError) && (
          <div className="px-3.5 py-[9px] bg-red-50 border border-red-200 rounded-md text-[12px] text-negative flex items-start gap-3">
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
          <div className="flex items-start gap-3 px-3.5 py-[11px] bg-red-100 border-2 border-negative rounded-md text-negative">
            <span className="shrink-0 label-caps px-[7px] py-[3px] mt-px bg-negative text-white rounded-[3px]">
              Unprotected
            </span>
            <p className="text-[13px] font-semibold text-pretty">
              {status.unprotected} position{status.unprotected === 1 ? '' : 's'} filled with
              no confirmed stop at the broker. The engine retries protection every tick; new
              entries are held back until it succeeds.
            </p>
          </div>
        )}

        {!running && (
          <StartPanel
            preflight={engine.preflight}
            busy={engine.busy === 'start'}
            onStart={engine.start}
          />
        )}

        <div className="flex flex-wrap gap-3.5 items-start">
          <div className="flex-[999_1_680px] min-w-0 flex flex-col gap-3.5">
            {running && <PnlSummary status={status} open={open} />}
            <PositionsCard
              open={open}
              closed={positions?.closed ?? []}
              skipped={positions?.rejected ?? []}
              totalRealised={positions?.total_realised_pnl}
              markStale={markStale}
              feedState={positions?.live_pnl_feed_state}
              feedReason={positions?.live_pnl_feed_reason}
              busy={engine.busy}
              onClose={engine.closePosition}
              onInspect={(row, kind) => setInspecting({ row, kind })}
            />
          </div>

          {running && status && (
            <aside className="flex-[1_1_320px] max-w-full flex flex-col gap-3.5">
              <RiskRail status={status} />
              <DangerZone
                status={status}
                busy={engine.busy}
                onStopEntries={engine.stopEntries}
                onStartEntries={engine.startEntries}
                onKillAll={engine.killAll}
              />
            </aside>
          )}
        </div>
      </div>

      {drawer && (
        <EventDrawer row={drawer.row} kind={drawer.kind} onClose={closeDrawer} />
      )}
    </div>
  )
}

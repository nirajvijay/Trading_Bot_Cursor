import type { ArbDayStatus } from '../../api/types'
import { inr, pnlClass, reasonLabel, stageLabel, timeIst } from './format'

function Cell({
  label,
  value,
  tone,
  note,
}: {
  label: string
  value: React.ReactNode
  tone?: string
  note?: React.ReactNode
}) {
  return (
    <div className="bg-surface px-4 py-3 flex flex-col gap-1 min-w-0">
      <p className="label-caps text-on-surface-variant">{label}</p>
      <p className={`font-data tabular-nums text-[17px] font-medium leading-[1.3] ${tone ?? ''}`}>
        {value}
      </p>
      {note && <p className="text-[10px] text-on-surface-variant text-pretty">{note}</p>}
    </div>
  )
}

/** Where the day P&L sits between the hard stop and the day-lock arming point. */
function DayBar({ arb }: { arb: ArbDayStatus }) {
  const low = -arb.hard_day_stop
  const high = Math.max(arb.lock_arm_at, arb.peak_mtm ?? 0, arb.day_mtm ?? 0) * 1.1
  const at = (value: number) =>
    `${Math.min(100, Math.max(0, ((value - low) / (high - low)) * 100))}%`
  const mtm = arb.day_mtm
  return (
    <div className="relative h-[18px] mx-4 mb-3">
      <div className="absolute inset-x-0 top-[7px] h-1 rounded bg-surface-container" />
      <div
        className="absolute top-[7px] h-1 rounded-l bg-red-200"
        style={{ left: 0, width: at(0) }}
      />
      <div className="absolute top-[3px] w-px h-3 bg-on-surface-variant" style={{ left: at(0) }} />
      <div
        title={`Day lock arms at ${inr(arb.lock_arm_at, 0)}`}
        className="absolute top-[3px] w-px h-3 bg-positive"
        style={{ left: at(arb.lock_arm_at) }}
      />
      {arb.lock_line != null && (
        <div
          title={`Flatten at or below ${inr(arb.lock_line, 0)}`}
          className="absolute top-0 w-[3px] h-[18px] bg-amber-500 rounded"
          style={{ left: at(arb.lock_line) }}
        />
      )}
      {mtm != null && (
        <div
          title={`Day P&L ${inr(mtm, 0)}`}
          className={`absolute top-[2px] size-[14px] -ml-[7px] rounded-full border-2 border-white ${
            mtm < 0 ? 'bg-negative' : 'bg-positive'
          }`}
          style={{ left: at(mtm) }}
        />
      )}
    </div>
  )
}

/** ARB's risk budget, as the engine computed it this tick. */
export function ArbBudgetStrip({ arb }: { arb: ArbDayStatus }) {
  if (arb.error) {
    return (
      <section className="px-3.5 py-[9px] bg-red-50 border border-red-200 rounded-md text-[12px] text-negative">
        ARB status could not be read this tick: {arb.error}
      </section>
    )
  }
  const mtm = arb.day_mtm
  const cap = arb.open_risk_cap + arb.house_money
  return (
    <section className="border border-outline-variant rounded-md overflow-hidden bg-outline-variant flex flex-col gap-px">
      <div className="bg-surface px-4 py-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className="text-[13px] font-extrabold uppercase tracking-tight">ARB risk budget</h2>
        <span className="label-caps px-2 py-[3px] rounded border border-[#cfe0ff] bg-surface-container-low text-primary">
          {stageLabel(arb.risk_scale)}
        </span>
        <span className="text-[11px] text-on-surface-variant">
          ORB until {arb.orb_cutoff} · funnel until {arb.funnel_cutoff}
        </span>
        {!arb.day_check_ok && !arb.halted_reason && (
          <span className="label-caps px-2 py-[3px] rounded bg-negative text-white">
            Day controls failing: no entries
          </span>
        )}
      </div>

      {arb.halted_reason && (
        <div className="bg-amber-50 px-4 py-2.5 text-[12px] font-semibold text-amber-900">
          Stopped for the day: {reasonLabel(arb.halted_reason)} at {timeIst(arb.halted_at)}, day
          P&L {inr(arb.halted_mtm, 0)}. Nothing more is traded today.
        </div>
      )}

      <div className="grid grid-cols-[repeat(auto-fit,minmax(165px,1fr))] gap-px">
        <Cell
          label="Day P&L after charges"
          value={inr(mtm, 0)}
          tone={pnlClass(mtm)}
          note={
            arb.prices_complete === false
              ? `No fresh price for ${arb.stale_symbols.join(', ')}: counted at the stop`
              : `Closed ${inr(arb.closed_net, 0)} · open ${inr(arb.open_net, 0)} · peak ${inr(arb.peak_mtm, 0)}`
          }
        />
        <Cell
          label="Hard day stop"
          value={`−${inr(arb.hard_day_stop, 0)}`}
          note={
            mtm == null
              ? 'Flattens everything at this day P&L'
              : `${inr(Math.max(0, mtm + arb.hard_day_stop), 0)} away`
          }
        />
        <Cell
          label="Day lock"
          value={arb.lock_armed ? `Armed: ${inr(arb.lock_line, 0)}` : 'Not armed'}
          tone={arb.lock_armed ? 'text-amber-700' : 'text-on-surface-variant'}
          note={
            arb.lock_armed
              ? `Flattens if a 1-minute check finds the day at or below ${Math.round(arb.lock_keep * 100)}% of the peak`
              : `Arms once the day reaches ${inr(arb.lock_arm_at, 0)}`
          }
        />
        <Cell
          label="Open risk"
          value={`${inr(arb.live_risk, 0)} / ${inr(cap, 0)}`}
          note={`Cap ${inr(arb.open_risk_cap, 0)}${arb.house_money > 0 ? ` + ${inr(arb.house_money, 0)} of today's profit` : ''} · room ${inr(Math.max(0, arb.room), 0)}`}
          tone={arb.room < arb.min_risk ? 'text-negative' : undefined}
        />
        <Cell
          label="Size"
          value={arb.half_size ? 'Half' : 'Full'}
          tone={arb.half_size ? 'text-amber-700' : undefined}
          note={`${arb.losers_today} losing trade${arb.losers_today === 1 ? '' : 's'} today · score-0 used ${arb.score0_used}/${arb.score0_max}`}
        />
      </div>
      <div className="bg-surface pt-2">
        <DayBar arb={arb} />
        <p className="px-4 pb-2.5 -mt-1.5 flex flex-wrap gap-x-4 text-[10px] text-on-surface-variant">
          <span>Left edge: hard stop −{inr(arb.hard_day_stop, 0)}</span>
          <span>
            <span className="inline-block size-2 rounded-full bg-on-surface-variant align-middle" /> day
            P&L now
          </span>
          <span>
            <span className="inline-block w-px h-2.5 bg-positive align-middle" /> lock arms at{' '}
            {inr(arb.lock_arm_at, 0)}
          </span>
          {arb.lock_line != null && (
            <span>
              <span className="inline-block w-[3px] h-2.5 bg-amber-500 rounded align-middle" /> flatten
              line {inr(arb.lock_line, 0)}
            </span>
          )}
        </p>
      </div>
    </section>
  )
}

/** "ORB" or "F0".."F3": which ARB source a trade came from. */
export function SourceBadge({ source }: { source: string }) {
  const orb = source === 'ORB'
  return (
    <span
      title={orb ? 'Opening range breakout' : `Funnel trigger, quality score ${source.slice(1)}`}
      className={`label-caps inline-block w-[68px] text-center shrink-0 py-0.5 rounded border ${
        orb
          ? 'text-violet-800 border-violet-200 bg-violet-50'
          : 'text-primary border-[#cfe0ff] bg-surface-container-low'
      }`}
    >
      {source}
    </span>
  )
}

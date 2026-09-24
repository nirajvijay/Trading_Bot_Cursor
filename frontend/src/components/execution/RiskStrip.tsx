import type { ExecutionPosition, ExecutionStatus } from '../../api/types'
import {
  STALE_MARK_SECONDS,
  ageLabel,
  inr,
  lakhs,
  pnlClass,
  secondsSince,
} from './format'
import { LivePnlBadge } from './LivePnlBadge'

function Tile({
  label,
  value,
  tone,
  note,
  badge,
  hero,
}: {
  label: string
  value: string
  tone?: string
  note?: string
  badge?: React.ReactNode
  hero?: boolean
}) {
  return (
    <div className="bg-surface px-4 py-3.5 flex flex-col gap-1">
      <div className="flex items-center gap-1.5 flex-wrap">
        <p className="label-caps text-on-surface-variant">{label}</p>
        {badge}
      </div>
      <p
        className={`font-data tabular-nums ${
          hero ? 'text-[26px] font-semibold leading-[1.15]' : 'text-[20px] font-medium leading-[1.3]'
        } ${tone ?? ''}`}
      >
        {value}
      </p>
      {note && <p className="text-[10px] text-on-surface-variant">{note}</p>}
    </div>
  )
}

/** The day's P&L, split the way it is reconciled: realised + ongoing. */
export function PnlSummary({
  status,
  open,
}: {
  status: ExecutionStatus | null
  open: ExecutionPosition[]
}) {
  const markAge = secondsSince(status?.live_pnl_as_of)
  const markStale = markAge === null || markAge > STALE_MARK_SECONDS
  // Older APIs send only total_live_pnl; the ongoing total is the same figure.
  const ongoingPnl = status?.total_ongoing_pnl ?? status?.total_live_pnl ?? null
  const dayPnl = status?.total_day_pnl ?? null
  const riskTaken = open.reduce((sum, r) => sum + (r.risk_taken_rupees ?? 0), 0)

  return (
    <section className="grid grid-cols-[repeat(auto-fit,minmax(210px,1fr))] gap-px bg-outline-variant border border-outline-variant rounded-md overflow-hidden">
      <Tile
        hero
        label="Total Day P&L"
        value={inr(dayPnl)}
        tone={markStale ? 'text-on-surface-variant' : pnlClass(dayPnl)}
        note="realised + ongoing"
      />
      <Tile
        label="Total Realised P&L"
        value={inr(status?.total_realised_pnl)}
        tone={pnlClass(status?.total_realised_pnl)}
        note="closed trades"
      />
      <Tile
        label="Total Ongoing P&L"
        value={inr(ongoingPnl)}
        tone={markStale ? 'text-on-surface-variant' : pnlClass(ongoingPnl)}
        badge={
          <LivePnlBadge
            state={status?.live_pnl_feed_state}
            reason={status?.live_pnl_feed_reason}
            orderUpdates={status?.live_pnl_order_updates}
          />
        }
        note={
          markStale
            ? `stale · ${ageLabel(markAge)}`
            : status?.live_pnl_complete
              ? `open trades · ${ageLabel(markAge)}`
              : 'partial marks'
        }
      />
      <Tile
        label="Total Risk Taken"
        value={inr(Math.round(riskTaken * 100) / 100)}
        note="sum of risk on open trades"
      />
    </section>
  )
}

/** Exposure and remaining room: the loss cap as a meter, then capital. */
export function RiskRail({ status }: { status: ExecutionStatus | null }) {
  const cap = status?.daily_loss_cap ?? 0
  const lost = status?.realised_loss_today ?? 0
  const usedPct = cap > 0 ? Math.min(100, (lost / cap) * 100) : 0
  const breached = cap > 0 && lost >= cap
  const capital = status?.capital ?? null
  const openN = status?.open_positions ?? 0
  const unprotected = status?.unprotected ?? 0

  return (
    <>
      <section className="bg-surface border border-outline-variant rounded-md px-4 py-3.5 flex flex-col gap-2.5">
        <div className="flex justify-between items-baseline gap-2">
          <p className="label-caps text-on-surface-variant">Realised loss today</p>
          <span className="font-data text-[11px] text-on-surface-variant">
            {Math.round(usedPct)}% of cap
          </span>
        </div>
        <div className="flex items-baseline gap-1.5">
          <span className="font-data text-[22px] font-semibold tabular-nums">{inr(lost)}</span>
          <span className="font-data text-[12px] text-on-surface-variant">of {inr(cap)}</span>
        </div>
        {/* Proximity to the hard daily cap is the one number worth showing
            as a shape rather than only as digits. */}
        <div
          className="h-2 w-full bg-surface-container-low rounded overflow-hidden"
          role="meter"
          aria-valuenow={Math.round(usedPct)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Daily loss cap used"
        >
          <div
            className={`h-full rounded transition-[width] duration-500 ${
              breached ? 'bg-negative' : usedPct > 66 ? 'bg-amber-500' : 'bg-primary'
            }`}
            style={{ width: `${Math.max(usedPct, lost > 0 ? 2 : 0)}%` }}
          />
        </div>
        <div className="flex justify-between text-[11px] text-on-surface-variant">
          <span>Daily loss cap</span>
          <span className="font-data text-on-surface font-semibold">
            {inr(status?.remaining_daily ?? 0)} left
          </span>
        </div>
        {breached && (
          <p className="text-[12px] font-semibold text-negative">
            Daily cap breached. Entries are off for the rest of the session and every
            position is being closed.
          </p>
        )}
      </section>

      <section className="grid grid-cols-2 gap-px bg-outline-variant border border-outline-variant rounded-md overflow-hidden">
        <div className="bg-surface px-3.5 py-3 flex flex-col gap-[3px]">
          <p className="label-caps text-on-surface-variant">Open positions</p>
          <p className="font-data text-[20px] tabular-nums">{openN}</p>
          <p className="text-[10px] text-on-surface-variant">
            {openN ? 'being reconciled each tick' : ' '}
          </p>
        </div>
        <div className={`px-3.5 py-3 flex flex-col gap-[3px] ${unprotected ? 'bg-red-50' : 'bg-surface'}`}>
          <p className="label-caps text-on-surface-variant">Unprotected</p>
          <p
            className={`font-data text-[20px] tabular-nums ${
              unprotected ? 'text-negative font-bold' : ''
            }`}
          >
            {unprotected}
          </p>
          <p className="text-[10px] text-on-surface-variant">
            {unprotected ? 'filled, no confirmed stop' : 'all stops confirmed'}
          </p>
        </div>
      </section>

      <section className="bg-surface border border-outline-variant rounded-md overflow-hidden">
        <header className="px-4 py-2.5 border-b border-outline-variant">
          <p className="label-caps text-on-surface-variant">Capital</p>
        </header>
        <dl className="flex flex-col">
          <CapitalRow
            label="Buying power"
            note={
              capital
                ? `${lakhs(capital.total_capital_rupees)} × ${capital.leverage_factor}x`
                : undefined
            }
            value={lakhs(capital?.buying_power_rupees)}
          />
          <CapitalRow label="Margin used" value={inr(capital?.margin_used_rupees, 0)} />
          <CapitalRow
            label="Capital left"
            note={
              capital ? `${lakhs(capital.remaining_buying_power_rupees)} to deploy` : undefined
            }
            value={inr(capital?.remaining_capital_rupees, 0)}
          />
        </dl>
      </section>
    </>
  )
}

function CapitalRow({ label, note, value }: { label: string; note?: string; value: string }) {
  return (
    <div className="flex justify-between items-baseline gap-3 px-4 py-2.5 border-b border-[#eef0f4] last:border-b-0">
      <dt className="text-[12px] text-on-surface-variant">
        {label}
        {note && <span className="block font-data text-[10px] mt-0.5">{note}</span>}
      </dt>
      <dd className="font-data text-[15px] tabular-nums">{value}</dd>
    </div>
  )
}

import type { ExecutionStatus } from '../../api/types'
import {
  STALE_MARK_SECONDS,
  ageLabel,
  inr,
  lakhs,
  pnlClass,
  secondsSince,
} from './format'
import { LivePnlBadge } from './LivePnlBadge'

function Metric({
  label,
  value,
  tone,
  note,
  badge,
}: {
  label: string
  value: string
  tone?: string
  note?: string
  badge?: React.ReactNode
}) {
  return (
    <div className="bg-surface border border-outline-variant rounded-sm px-3 py-2">
      <div className="flex items-center gap-1.5 flex-wrap">
        <p className="label-caps text-on-surface-variant">{label}</p>
        {badge}
      </div>
      <p className={`font-data text-[15px] tabular-nums leading-tight ${tone ?? ''}`}>
        {value}
      </p>
      {note && <p className="text-[10px] text-on-surface-variant mt-0.5">{note}</p>}
    </div>
  )
}

/** Exposure and remaining room, at a glance. */
export function RiskStrip({ status }: { status: ExecutionStatus | null }) {
  const cap = status?.daily_loss_cap ?? 0
  const lost = status?.realised_loss_today ?? 0
  const usedPct = cap > 0 ? Math.min(100, (lost / cap) * 100) : 0
  const breached = cap > 0 && lost >= cap
  const markAge = secondsSince(status?.live_pnl_as_of)
  const markStale = markAge === null || markAge > STALE_MARK_SECONDS
  const capital = status?.capital ?? null
  // Older APIs send only total_live_pnl; the ongoing total is the same figure.
  const ongoingPnl = status?.total_ongoing_pnl ?? status?.total_live_pnl ?? null
  const dayPnl = status?.total_day_pnl ?? null

  return (
    <section className="flex flex-col gap-2">
      <div className="bg-surface border border-outline-variant rounded-sm px-4 py-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="label-caps text-on-surface-variant">Realised loss today</p>
          <p className="font-data text-[11px] text-on-surface-variant tabular-nums">
            {inr(lost)} of {inr(cap)} · {inr(status?.remaining_daily ?? 0)} left
          </p>
        </div>
        {/* Proximity to the hard daily cap is the one number worth showing
            as a shape rather than only as digits. */}
        <div
          className="mt-2 h-1.5 w-full bg-surface-container-low rounded-full overflow-hidden"
          role="meter"
          aria-valuenow={Math.round(usedPct)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Daily loss cap used"
        >
          <div
            className={`h-full rounded-full transition-[width] duration-500 ${
              breached
                ? 'bg-negative'
                : usedPct > 66
                  ? 'bg-amber-500'
                  : 'bg-primary'
            }`}
            style={{ width: `${Math.max(usedPct, lost > 0 ? 2 : 0)}%` }}
          />
        </div>
        {breached && (
          <p className="mt-2 text-[12px] font-semibold text-negative">
            Daily cap breached. Entries are off for the rest of the session and every
            position is being closed.
          </p>
        )}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-2">
        <Metric
          label="Total Day P&L"
          value={inr(dayPnl)}
          tone={markStale ? 'text-on-surface-variant' : pnlClass(dayPnl)}
          note="realised + ongoing"
        />
        <Metric
          label="Total Realised P&L"
          value={inr(status?.total_realised_pnl)}
          tone={pnlClass(status?.total_realised_pnl)}
          note="closed trades"
        />
        <Metric
          label="Total Ongoing P&L"
          value={inr(ongoingPnl)}
          tone={markStale ? 'text-on-surface-variant' : pnlClass(ongoingPnl)}
          badge={
            <LivePnlBadge
              state={status?.live_pnl_feed_state}
              reason={status?.live_pnl_feed_reason}
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
        <Metric
          label="Open positions"
          value={String(status?.open_positions ?? 0)}
          note={status?.open_positions ? 'being reconciled each tick' : undefined}
        />
        <Metric
          label="Unprotected"
          value={String(status?.unprotected ?? 0)}
          tone={status?.unprotected ? 'text-negative font-semibold' : undefined}
          note={status?.unprotected ? 'filled, no confirmed stop' : 'all stops confirmed'}
        />
        <Metric
          label="Buying power"
          value={lakhs(capital?.buying_power_rupees)}
          note={
            capital
              ? `${lakhs(capital.total_capital_rupees)} × ${capital.leverage_factor}x`
              : undefined
          }
        />
        <Metric label="Margin used" value={inr(capital?.margin_used_rupees, 0)} />
        <Metric
          label="Capital left"
          value={inr(capital?.remaining_capital_rupees, 0)}
          note={
            capital
              ? `${lakhs(capital.remaining_buying_power_rupees)} to deploy`
              : undefined
          }
        />
      </div>
    </section>
  )
}

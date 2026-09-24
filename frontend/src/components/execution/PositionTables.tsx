import type { ExecutionPosition, LivePnlFeedState } from '../../api/types'
import {
  STALE_MARK_SECONDS,
  inr,
  isStuckPendingEntry,
  isUnprotected,
  mismatchTitle,
  num,
  pnlClass,
  reasonLabel,
  stateLabel,
  timeIst,
} from './format'
import { LivePnlBadge } from './LivePnlBadge'

function Th({
  children,
  align = 'left',
}: {
  children?: React.ReactNode
  align?: 'left' | 'right'
}) {
  return (
    <th
      className={`px-3 py-2 label-caps font-bold whitespace-nowrap ${
        align === 'right' ? 'text-right' : 'text-left'
      }`}
    >
      {children}
    </th>
  )
}

function Empty({ cols, children }: { cols: number; children: React.ReactNode }) {
  return (
    <tr>
      <td colSpan={cols} className="px-3 py-6 text-center text-[12px] text-on-surface-variant">
        {children}
      </td>
    </tr>
  )
}

function TableShell({
  title,
  count,
  children,
  note,
}: {
  title: string
  count: number
  children: React.ReactNode
  note?: React.ReactNode
}) {
  return (
    <section className="bg-surface border border-outline-variant rounded-sm overflow-hidden">
      <header className="px-4 py-2.5 border-b border-outline-variant flex items-baseline gap-2">
        <h2 className="text-[12px] font-extrabold uppercase tracking-tight">{title}</h2>
        <span className="font-data text-[11px] text-on-surface-variant tabular-nums">
          {count}
        </span>
        {note && (
          <span className="ml-auto text-[10px] text-on-surface-variant flex items-center gap-2">
            {note}
          </span>
        )}
      </header>
      <div className="overflow-x-auto custom-scrollbar">
        <table className="w-full text-[12px]">{children}</table>
      </div>
    </section>
  )
}

function Side({ direction }: { direction: string }) {
  const long = direction === 'UP'
  return (
    <span
      className={`label-caps px-1.5 py-0.5 rounded-sm border ${
        long
          ? 'text-positive border-emerald-200 bg-emerald-50'
          : 'text-negative border-red-200 bg-red-50'
      }`}
    >
      {long ? 'Long' : 'Short'}
    </span>
  )
}

function Tier({ tier }: { tier: string | null }) {
  if (!tier) return <span className="text-on-surface-variant">—</span>
  return (
    <span
      className={`label-caps px-1.5 py-0.5 rounded-sm border ${
        tier === 'ACCEPT'
          ? 'text-primary border-[#cfe0ff] bg-surface-container-low'
          : 'text-amber-800 border-amber-200 bg-amber-50'
      }`}
    >
      {tier}
    </span>
  )
}

export function OpenPositionsTable({
  rows,
  markStale,
  feedState,
  feedReason,
  busy,
  onClose,
  onInspect,
}: {
  rows: ExecutionPosition[]
  markStale: boolean
  feedState?: LivePnlFeedState | null
  feedReason?: string | null
  busy: string | null
  onClose: (tradeId: string) => void
  onInspect: (tradeId: string) => void
}) {
  return (
    <TableShell
      title="Open"
      count={rows.length}
      note={
        <>
          <LivePnlBadge state={feedState} reason={feedReason} />
          <span>click a row for its event diary</span>
        </>
      }
    >
      <thead className="bg-surface-container-low text-on-surface-variant">
        <tr>
          <Th>Symbol</Th>
          <Th>Side</Th>
          <Th>Tier</Th>
          <Th align="right">Qty</Th>
          <Th align="right">Entry</Th>
          <Th align="right">Stop</Th>
          <Th align="right">Risk taken</Th>
          <Th align="right">Ongoing P&L (live)</Th>
          <Th align="right">Stock Day Total</Th>
          <Th>State</Th>
          <Th />
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && (
          <Empty cols={11}>No open positions.</Empty>
        )}
        {rows.map((row) => {
          const unprotected = isUnprotected(row.state)
          const stuck = isStuckPendingEntry(row.state, row.updated_at)
          return (
            <tr
              key={row.trade_id}
              onClick={() => onInspect(row.trade_id)}
              className={`terminal-row border-t border-outline-variant cursor-pointer hover:bg-surface-container-low ${
                unprotected || stuck ? '!bg-red-50' : ''
              }`}
            >
              <td className="px-3 py-2 font-data font-semibold whitespace-nowrap">
                {row.tradingsymbol}
              </td>
              <td className="px-3 py-2">
                <Side direction={row.direction} />
              </td>
              <td className="px-3 py-2">
                <Tier tier={row.vwap_classification} />
              </td>
              <td className="px-3 py-2 text-right font-data tabular-nums">
                {row.qty || '—'}
              </td>
              <td className="px-3 py-2 text-right font-data tabular-nums">
                {num(row.entry_price)}
              </td>
              <td className="px-3 py-2 text-right font-data tabular-nums">
                {num(row.stop_price)}
                {row.stop_adopted_from_broker && (
                  <span
                    title="Adopted from a change made directly in Kite"
                    className="ml-1 text-primary font-bold"
                  >
                    ↺
                  </span>
                )}
              </td>
              <td className="px-3 py-2 text-right font-data tabular-nums text-on-surface-variant">
                {inr(row.risk_taken_rupees)}
              </td>
              <td
                title={
                  row.live_pnl_source === 'kite_rest' && feedState && feedState !== 'off'
                    ? `Kite REST fallback: ${reasonLabel(row.live_pnl_reason?.split(':')[0])}`
                    : undefined
                }
                className={`px-3 py-2 text-right font-data tabular-nums ${
                  markStale ? 'text-on-surface-variant' : pnlClass(row.live_pnl)
                }`}
              >
                {inr(row.live_pnl)}
                {row.live_pnl_source === 'kite_rest' && feedState && feedState !== 'off' && (
                  <span className="ml-1 label-caps text-amber-800">rest</span>
                )}
              </td>
              <td
                className={`px-3 py-2 text-right font-data tabular-nums ${
                  markStale ? 'text-on-surface-variant' : pnlClass(row.stock_day_total)
                }`}
              >
                {inr(row.stock_day_total)}
              </td>
              <td className="px-3 py-2 whitespace-nowrap">
                <span className={unprotected || stuck ? 'text-negative font-semibold' : ''}>
                  {stuck
                    ? 'Stuck — never reached broker'
                    : row.exiting
                      ? 'Exiting'
                      : stateLabel(row.state)}
                </span>
                {row.manual_review && (
                  <span className="ml-1.5 label-caps text-negative">review</span>
                )}
              </td>
              <td className="px-3 py-2 text-right">
                <button
                  type="button"
                  disabled={busy === `close_position:${row.trade_id}`}
                  onClick={(e) => {
                    e.stopPropagation()
                    onClose(row.trade_id)
                  }}
                  className="label-caps px-2 py-1 rounded-sm border border-outline-variant bg-surface hover:bg-surface-container-low disabled:text-on-surface-variant"
                >
                  {busy === `close_position:${row.trade_id}` ? 'Closing…' : 'Close'}
                </button>
              </td>
            </tr>
          )
        })}
      </tbody>
    </TableShell>
  )
}

export function ClosedPositionsTable({
  rows,
  totalRealised,
  onInspect,
}: {
  rows: ExecutionPosition[]
  /** Kite's figure for flat stocks; see the API's desk P&L rule. */
  totalRealised?: number | null
  onInspect: (tradeId: string) => void
}) {
  const total =
    totalRealised ?? rows.reduce((sum, r) => sum + (r.realised_pnl ?? 0), 0)
  return (
    <TableShell
      title="Closed"
      count={rows.length}
      note={rows.length ? `realised ${inr(total)}` : undefined}
    >
      <thead className="bg-surface-container-low text-on-surface-variant">
        <tr>
          <Th>Symbol</Th>
          <Th>Side</Th>
          <Th align="right">Qty</Th>
          <Th align="right">Entry fill</Th>
          <Th>Closed by</Th>
          <Th align="right">Realised P&L</Th>
          <Th align="right">Stock Day Total</Th>
          <Th align="right">At</Th>
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && <Empty cols={8}>Nothing closed yet.</Empty>}
        {rows.map((row) => {
          const surprising =
            row.close_reason === 'manual_broker_intervention' ||
            row.close_reason === 'unattributed' ||
            row.close_reason === 'abnormal_slippage_flatten'
          return (
            <tr
              key={row.trade_id}
              onClick={() => onInspect(row.trade_id)}
              className="terminal-row border-t border-outline-variant cursor-pointer hover:bg-surface-container-low"
            >
              <td className="px-3 py-2 font-data font-semibold">{row.tradingsymbol}</td>
              <td className="px-3 py-2">
                <Side direction={row.direction} />
              </td>
              <td className="px-3 py-2 text-right font-data tabular-nums">{row.qty}</td>
              <td className="px-3 py-2 text-right font-data tabular-nums">
                {num(row.entry_price)}
              </td>
              <td className="px-3 py-2">
                <span className={surprising ? 'text-negative font-semibold' : ''}>
                  {reasonLabel(row.close_reason)}
                </span>
              </td>
              <td
                className={`px-3 py-2 text-right font-data tabular-nums font-semibold ${pnlClass(
                  row.realised_pnl,
                )}`}
              >
                {row.realised_unattributed ? (
                  <span className="text-negative font-semibold whitespace-nowrap">
                    unattributed — check Kite
                  </span>
                ) : (
                  inr(row.realised_pnl)
                )}
              </td>
              <td
                className={`px-3 py-2 text-right font-data tabular-nums ${pnlClass(
                  row.stock_day_total,
                )}`}
              >
                {row.pnl_mismatch && (
                  <span
                    role="img"
                    aria-label={mismatchTitle(row.pnl_mismatch)}
                    title={mismatchTitle(row.pnl_mismatch)}
                    className="mr-1 text-amber-700 font-bold cursor-help"
                  >
                    ⚠
                  </span>
                )}
                {inr(row.stock_day_total)}
              </td>
              <td className="px-3 py-2 text-right font-data tabular-nums text-on-surface-variant">
                {timeIst(row.updated_at)}
              </td>
            </tr>
          )
        })}
      </tbody>
    </TableShell>
  )
}

export function SkippedPositionsTable({
  rows,
  onInspect,
}: {
  rows: ExecutionPosition[]
  onInspect: (tradeId: string) => void
}) {
  return (
    <TableShell title="Skipped & rejected" count={rows.length}>
      <thead className="bg-surface-container-low text-on-surface-variant">
        <tr>
          <Th>Symbol</Th>
          <Th>Tier</Th>
          <Th>Reason</Th>
          <Th>State</Th>
          <Th align="right">At</Th>
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && <Empty cols={5}>Nothing skipped.</Empty>}
        {rows.map((row) => (
          <tr
            key={row.trade_id}
            onClick={() => onInspect(row.trade_id)}
            className="terminal-row border-t border-outline-variant cursor-pointer hover:bg-surface-container-low"
          >
            <td className="px-3 py-2 font-data font-semibold">{row.tradingsymbol}</td>
            <td className="px-3 py-2">
              <Tier tier={row.vwap_classification} />
            </td>
            <td className="px-3 py-2">{reasonLabel(row.skip_reason)}</td>
            <td className="px-3 py-2 text-on-surface-variant">
              {stateLabel(row.state)}
            </td>
            <td className="px-3 py-2 text-right font-data tabular-nums text-on-surface-variant">
              {timeIst(row.created_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </TableShell>
  )
}

export { STALE_MARK_SECONDS }

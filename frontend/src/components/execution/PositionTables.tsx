import { useState } from 'react'
import type { ExecutionPosition, LivePnlFeedState } from '../../api/types'
import {
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

export type PositionKind = 'open' | 'closed' | 'skipped'

function Th({
  children,
  align = 'left',
  divider,
  edge,
}: {
  children?: React.ReactNode
  align?: 'left' | 'right'
  divider?: boolean
  edge?: boolean
}) {
  return (
    <th
      className={`${edge ? 'px-3.5' : 'px-3'} py-2 label-caps whitespace-nowrap border-b border-outline-variant ${
        align === 'right' ? 'text-right' : 'text-left'
      } ${divider ? 'border-l' : ''}`}
    >
      {children}
    </th>
  )
}

function Empty({ cols, children }: { cols: number; children: React.ReactNode }) {
  return (
    <tr>
      <td colSpan={cols} className="px-3 py-7 text-center text-[12px] text-on-surface-variant">
        {children}
      </td>
    </tr>
  )
}

const ROW = 'border-b border-[#eef0f4] cursor-pointer hover:!bg-surface-container-low'
const CELL = 'px-3 py-2.5'
const NUM = `${CELL} text-right font-data tabular-nums`
const DIVIDER = 'border-l border-[#eef0f4]'

export function Side({ direction }: { direction: string }) {
  const long = direction === 'UP'
  return (
    <span
      className={`label-caps inline-block w-[50px] text-center shrink-0 py-0.5 rounded border ${
        long
          ? 'text-positive border-emerald-200 bg-emerald-50'
          : 'text-negative border-red-200 bg-red-50'
      }`}
    >
      {long ? 'Long' : 'Short'}
    </span>
  )
}

export function Tier({ tier }: { tier: string | null }) {
  if (!tier) {
    return (
      <span className="label-caps inline-block w-[68px] text-center shrink-0 text-on-surface-variant">
        —
      </span>
    )
  }
  return (
    <span
      className={`label-caps inline-block w-[68px] text-center shrink-0 py-0.5 rounded border ${
        tier === 'ACCEPT'
          ? 'text-primary border-[#cfe0ff] bg-surface-container-low'
          : 'text-amber-800 border-amber-200 bg-amber-50'
      }`}
    >
      {tier}
    </span>
  )
}

function Instrument({
  symbol,
  children,
}: {
  symbol: string
  children?: React.ReactNode
}) {
  return (
    <td className="px-3.5 py-2.5 whitespace-nowrap">
      <div className="flex items-center gap-2">
        <span className="font-data font-bold text-[12px] min-w-[92px]">{symbol}</span>
        {children}
      </div>
    </td>
  )
}

/** Amber marker for a row whose Ongoing P&L is Kite REST, not the WebSocket. */
function RestFallback({ reason }: { reason: string | null | undefined }) {
  return (
    <span
      className="relative inline-flex items-center justify-center size-3.5 cursor-help group"
      onClick={(e) => e.stopPropagation()}
    >
      <span className="size-2 rounded-full bg-amber-500 shadow-[0_0_0_3px_#fde68a]" />
      <span className="absolute bottom-[calc(100%+8px)] -right-2.5 z-20 hidden group-hover:flex w-[260px] flex-col gap-1 text-left whitespace-normal bg-on-surface text-white rounded-md px-[11px] py-[9px] shadow-[0_8px_20px_rgba(11,28,48,0.25)] font-sans font-normal">
        <span className="label-caps text-amber-200">Fallback: Kite REST</span>
        <span className="text-[11px] leading-[1.45]">
          WebSocket mark unavailable for this stock ({reasonLabel(reason?.split(':')[0])}).
          Ongoing P&amp;L falls back to Kite REST pnl, which is not live and is per stock for
          the day.
        </span>
      </span>
    </span>
  )
}

function OpenRows({
  rows,
  markStale,
  feedState,
  busy,
  onClose,
  onInspect,
}: {
  rows: ExecutionPosition[]
  markStale: boolean
  feedState?: LivePnlFeedState | null
  busy: string | null
  onClose: (tradeId: string) => void
  onInspect: (row: ExecutionPosition, kind: PositionKind) => void
}) {
  return (
    <table className="w-full border-collapse text-[12px]">
      <thead className="bg-background text-on-surface-variant">
        <tr>
          <Th edge>Instrument</Th>
          <Th align="right">Qty</Th>
          <Th align="right">Entry</Th>
          <Th align="right">Stop</Th>
          <Th align="right">Risk taken</Th>
          <Th align="right" divider>
            Ongoing P&L (live)
          </Th>
          <Th align="right">Stock Day Total</Th>
          <Th divider>State</Th>
          <Th edge />
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && <Empty cols={9}>No open positions.</Empty>}
        {rows.map((row) => {
          const stuck = isStuckPendingEntry(row.state, row.updated_at)
          const alarm = isUnprotected(row.state) || stuck
          const closing = busy === `close_position:${row.trade_id}`
          const rest = row.live_pnl_source === 'kite_rest' && !!feedState && feedState !== 'off'
          return (
            <tr
              key={row.trade_id}
              onClick={() => onInspect(row, 'open')}
              className={`${ROW} ${alarm ? 'bg-red-50' : 'bg-surface'}`}
            >
              <Instrument symbol={row.tradingsymbol}>
                <Side direction={row.direction} />
                <Tier tier={row.vwap_classification} />
              </Instrument>
              <td className={NUM}>{row.qty || '—'}</td>
              <td className={NUM}>{num(row.entry_price)}</td>
              <td className={`${NUM} whitespace-nowrap`}>
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
              <td className={`${NUM} text-on-surface-variant`}>{inr(row.risk_taken_rupees)}</td>
              <td
                className={`${NUM} font-semibold whitespace-nowrap ${DIVIDER} ${
                  markStale ? 'text-on-surface-variant' : pnlClass(row.live_pnl)
                }`}
              >
                <span className="inline-flex items-center justify-end gap-1.5">
                  {rest && <RestFallback reason={row.live_pnl_reason} />}
                  {inr(row.live_pnl)}
                </span>
              </td>
              <td
                className={`${NUM} ${
                  markStale ? 'text-on-surface-variant' : pnlClass(row.stock_day_total)
                }`}
              >
                {inr(row.stock_day_total)}
              </td>
              <td className={`${CELL} whitespace-nowrap ${DIVIDER}`}>
                <div className="flex items-center gap-[7px]">
                  <span
                    className={`size-[7px] rounded-full ${
                      alarm
                        ? 'bg-negative'
                        : row.state === 'protected' && !row.exiting
                          ? 'bg-positive'
                          : 'bg-amber-500'
                    }`}
                  />
                  <span className={alarm ? 'text-negative font-bold' : 'font-medium'}>
                    {stuck
                      ? 'Stuck — never reached broker'
                      : row.exiting
                        ? 'Exiting'
                        : stateLabel(row.state)}
                  </span>
                  {row.manual_review && (
                    <span className="label-caps text-negative px-[5px] py-px border border-red-200 rounded-[3px] bg-surface">
                      review
                    </span>
                  )}
                </div>
              </td>
              <td className="px-3.5 py-2.5 text-right">
                <button
                  type="button"
                  disabled={closing}
                  onClick={(e) => {
                    e.stopPropagation()
                    onClose(row.trade_id)
                  }}
                  className="label-caps px-2.5 py-[5px] rounded border border-outline-variant bg-surface hover:bg-surface-container-low hover:border-[#c4c7cf] disabled:text-on-surface-variant"
                >
                  {closing ? 'Closing…' : 'Close'}
                </button>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

const SURPRISING_CLOSE = new Set([
  'manual_broker_intervention',
  'unattributed',
  'abnormal_slippage_flatten',
])

function ClosedRows({
  rows,
  onInspect,
}: {
  rows: ExecutionPosition[]
  onInspect: (row: ExecutionPosition, kind: PositionKind) => void
}) {
  return (
    <table className="w-full border-collapse text-[12px]">
      <thead className="bg-background text-on-surface-variant">
        <tr>
          <Th edge>Instrument</Th>
          <Th align="right">Qty</Th>
          <Th align="right">Entry fill</Th>
          <Th>Closed by</Th>
          <Th align="right" divider>
            Realised P&L
          </Th>
          <Th align="right">Stock Day Total</Th>
          <Th align="right" divider edge>
            At
          </Th>
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && <Empty cols={7}>Nothing closed yet.</Empty>}
        {rows.map((row) => {
          const surprising = SURPRISING_CLOSE.has(row.close_reason ?? '')
          return (
            <tr
              key={row.trade_id}
              onClick={() => onInspect(row, 'closed')}
              className={`${ROW} bg-surface`}
            >
              <Instrument symbol={row.tradingsymbol}>
                <Side direction={row.direction} />
              </Instrument>
              <td className={NUM}>{row.qty}</td>
              <td className={NUM}>{num(row.entry_price)}</td>
              <td className={`${CELL} whitespace-nowrap`}>
                <span className={surprising ? 'text-negative font-bold' : ''}>
                  {reasonLabel(row.close_reason)}
                </span>
              </td>
              <td
                className={`${NUM} font-semibold whitespace-nowrap ${DIVIDER} ${
                  row.realised_unattributed ? 'text-negative' : pnlClass(row.realised_pnl)
                }`}
              >
                {row.realised_unattributed ? 'unattributed — check Kite' : inr(row.realised_pnl)}
              </td>
              <td className={`${NUM} whitespace-nowrap ${pnlClass(row.stock_day_total)}`}>
                {row.pnl_mismatch && (
                  <span
                    role="img"
                    aria-label={mismatchTitle(row.pnl_mismatch)}
                    title={mismatchTitle(row.pnl_mismatch)}
                    className="mr-[5px] text-amber-700 font-bold cursor-help"
                  >
                    ⚠
                  </span>
                )}
                {inr(row.stock_day_total)}
              </td>
              <td className={`px-3.5 py-2.5 text-right font-data tabular-nums text-on-surface-variant ${DIVIDER}`}>
                {timeIst(row.updated_at)}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function SkippedRows({
  rows,
  onInspect,
}: {
  rows: ExecutionPosition[]
  onInspect: (row: ExecutionPosition, kind: PositionKind) => void
}) {
  return (
    <table className="w-full border-collapse text-[12px]">
      <thead className="bg-background text-on-surface-variant">
        <tr>
          <Th edge>Instrument</Th>
          <Th>Reason</Th>
          <Th>State</Th>
          <Th align="right" divider edge>
            At
          </Th>
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && <Empty cols={4}>Nothing skipped.</Empty>}
        {rows.map((row) => (
          <tr
            key={row.trade_id}
            onClick={() => onInspect(row, 'skipped')}
            className={`${ROW} bg-surface`}
          >
            <Instrument symbol={row.tradingsymbol}>
              <Tier tier={row.vwap_classification} />
            </Instrument>
            <td className={CELL}>{reasonLabel(row.skip_reason)}</td>
            <td className={`${CELL} text-on-surface-variant`}>{stateLabel(row.state)}</td>
            <td className={`px-3.5 py-2.5 text-right font-data tabular-nums text-on-surface-variant ${DIVIDER}`}>
              {timeIst(row.created_at)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Open, closed and skipped trades behind one set of tabs. */
export function PositionsCard({
  open,
  closed,
  skipped,
  totalRealised,
  markStale,
  feedState,
  feedReason,
  busy,
  onClose,
  onInspect,
}: {
  open: ExecutionPosition[]
  closed: ExecutionPosition[]
  skipped: ExecutionPosition[]
  /** Kite's figure for flat stocks; see the API's desk P&L rule. */
  totalRealised?: number | null
  markStale: boolean
  feedState?: LivePnlFeedState | null
  feedReason?: string | null
  busy: string | null
  onClose: (tradeId: string) => void
  onInspect: (row: ExecutionPosition, kind: PositionKind) => void
}) {
  const [tab, setTab] = useState<PositionKind>('open')
  const realised =
    totalRealised ?? closed.reduce((sum, r) => sum + (r.realised_pnl ?? 0), 0)

  const tabs: [PositionKind, string, number][] = [
    ['open', 'Open', open.length],
    ['closed', 'Closed', closed.length],
    ['skipped', 'Skipped & rejected', skipped.length],
  ]

  return (
    <section className="bg-surface border border-outline-variant rounded-md overflow-hidden">
      <header className="px-3 py-2.5 border-b border-outline-variant flex flex-wrap items-center gap-2.5">
        <div role="tablist" className="flex gap-0.5 p-[3px] bg-surface-container-low rounded-md">
          {tabs.map(([id, label, count]) => {
            const active = tab === id
            return (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setTab(id)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded text-[12px] font-bold whitespace-nowrap ${
                  active
                    ? 'bg-surface text-on-surface shadow-[0_1px_2px_rgba(11,28,48,0.12)]'
                    : 'text-on-surface-variant'
                }`}
              >
                {label}
                <span
                  className={`font-data text-[11px] font-semibold px-1.5 py-px rounded-lg ${
                    active ? 'bg-surface-container' : 'bg-white/60'
                  }`}
                >
                  {count}
                </span>
              </button>
            )
          })}
        </div>
        <div className="ml-auto flex items-center gap-2 text-[11px] text-on-surface-variant">
          {tab === 'open' && (
            <>
              <LivePnlBadge state={feedState} reason={feedReason} />
              <span>click a row for its event diary</span>
            </>
          )}
          {tab === 'closed' && closed.length > 0 && (
            <span className="font-data">realised {inr(realised)}</span>
          )}
        </div>
      </header>

      <div className="overflow-x-auto custom-scrollbar">
        {tab === 'open' && (
          <OpenRows
            rows={open}
            markStale={markStale}
            feedState={feedState}
            busy={busy}
            onClose={onClose}
            onInspect={onInspect}
          />
        )}
        {tab === 'closed' && <ClosedRows rows={closed} onInspect={onInspect} />}
        {tab === 'skipped' && <SkippedRows rows={skipped} onInspect={onInspect} />}
      </div>
    </section>
  )
}

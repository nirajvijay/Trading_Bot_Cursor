import { Fragment, useEffect, useState } from 'react'
import type { ChargeBreakdown, TradeChargesRow } from '../../api/types'
import { useTradeCharges } from '../../hooks/useTradeCharges'
import { Side } from '../execution/PositionTables'
import { inr, num, pnlClass, timeIst } from '../execution/format'

/**
 * Charges tab: Kite's charges and net P&L per closed trade.
 *
 * Display only. The engine and the Execution Desk never read these numbers;
 * the daily loss cap keeps using gross P&L.
 */

const REASONS: Record<string, string> = {
  not_captured: 'Not captured: Kite keeps only today’s orders',
  entry_order_not_in_book: 'Entry order not in Kite’s book',
  exit_order_not_in_book: 'Exit order not in Kite’s book',
  no_exit_orders: 'No exit orders recorded',
  order_not_final: 'An order is still working',
  fill_unknown: 'Fill price not known yet',
  exit_does_not_match_entry: 'Exit does not match the entry',
  exits_do_not_cover_entry: 'Exits do not cover the entry',
  kite_session_expired: 'Kite login expired',
  kite_unavailable: 'Kite unavailable, try Refresh',
  kite_response_mismatch: 'Kite’s answer did not match, try Refresh',
}

function reasonText(reason: string | null): string {
  if (!reason) return '—'
  return REASONS[reason] ?? reason.replace(/_/g, ' ')
}

const BREAKDOWN: [keyof ChargeBreakdown, string][] = [
  ['brokerage', 'Brokerage'],
  ['stt', 'STT'],
  ['exchange', 'Exchange'],
  ['sebi', 'SEBI'],
  ['stamp_duty', 'Stamp duty'],
  ['gst', 'GST'],
]

function Tile({
  label,
  value,
  tone = 'text-on-surface',
  note,
  hero,
}: {
  label: string
  value: string
  tone?: string
  note?: string
  hero?: boolean
}) {
  return (
    <div className="bg-surface px-4 py-3 min-w-0">
      <div className="label-caps text-on-surface-variant">{label}</div>
      <div className={`font-data tabular-nums ${hero ? 'text-[22px]' : 'text-[18px]'} font-bold ${tone}`}>
        {value}
      </div>
      {note && <div className="text-[11px] text-on-surface-variant mt-0.5">{note}</div>}
    </div>
  )
}

function StatusCell({ row }: { row: TradeChargesRow }) {
  if (row.status === 'ok') return <span className="label-caps text-positive">Priced</span>
  if (row.status === 'paper') return <span className="label-caps text-on-surface-variant">Paper · no charges</span>
  return (
    <span className="text-[11px] text-on-surface-variant" title={row.reason ?? undefined}>
      {reasonText(row.reason)}
    </span>
  )
}

const CELL = 'px-3 py-2.5'
const NUM = `${CELL} text-right font-data tabular-nums`
const TH = 'px-3 py-2 label-caps whitespace-nowrap border-b border-outline-variant'

function todayIstDate(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date())
}

export function ChargesPage({ sessionDate }: { sessionDate: string }) {
  const [date, setDate] = useState(sessionDate)
  // Follow the shell's session date when it rolls over (midnight IST).
  useEffect(() => setDate(sessionDate), [sessionDate])
  const { data, loading, error, refresh } = useTradeCharges(date)
  const [open, setOpen] = useState<string | null>(null)
  const trades = data?.trades ?? []

  return (
    <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar">
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-4 flex flex-col gap-3">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-[16px] font-extrabold text-on-surface">Charges &amp; Net P&amp;L</h1>
            <p className="text-[12px] text-on-surface-variant">
              Kite’s charges per closed trade. Display only: the daily loss cap still uses gross P&amp;L.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="date"
              aria-label="Session date"
              className="bg-surface border border-outline-variant text-[12px] px-2 py-1 font-data"
              value={date}
              max={todayIstDate()}
              onChange={(e) => e.target.value && setDate(e.target.value)}
            />
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              className="label-caps px-3 py-1.5 border border-outline-variant bg-surface hover:bg-surface-container-low disabled:opacity-50"
            >
              {loading ? 'Loading…' : 'Refresh'}
            </button>
          </div>
        </header>

        {error && (
          <div className="px-3 py-2 bg-red-50 border border-red-200 text-red-800 text-[12px]">{error}</div>
        )}
        {data?.kite_error && (
          <div className="px-3 py-2 bg-amber-50 border border-amber-200 text-amber-900 text-[12px]">
            Could not price every trade: {reasonText(data.kite_error)}.
          </div>
        )}

        <section className="grid grid-cols-2 lg:grid-cols-4 gap-px bg-outline-variant border border-outline-variant rounded-md overflow-hidden">
          <Tile
            hero
            label="Net P&L"
            value={inr(data?.total_net_pnl)}
            tone={pnlClass(data?.total_net_pnl)}
            note="gross − charges"
          />
          <Tile label="Gross P&L" value={inr(data?.total_gross_pnl)} tone={pnlClass(data?.total_gross_pnl)} />
          <Tile label="Charges" value={inr(data?.total_charges)} note="brokerage, STT, fees, GST" />
          <Tile
            label="Priced"
            value={data ? `${data.priced_count} / ${data.live_closed_count}` : '—'}
            note="live closed trades · totals cover priced only"
          />
        </section>

        <section className="bg-surface border border-outline-variant rounded-md overflow-x-auto">
          <table className="w-full text-[12px] min-w-[860px]">
            <thead className="bg-surface-container-low text-on-surface-variant">
              <tr>
                <th className={`${TH} text-left`}>Closed</th>
                <th className={`${TH} text-left`}>Symbol</th>
                <th className={`${TH} text-right`}>Qty</th>
                <th className={`${TH} text-right`}>Entry avg</th>
                <th className={`${TH} text-right`}>Exit avg</th>
                <th className={`${TH} text-right`}>Gross</th>
                <th className={`${TH} text-right`}>Charges</th>
                <th className={`${TH} text-right`}>Net</th>
                <th className={`${TH} text-left`}>Status</th>
              </tr>
            </thead>
            <tbody>
              {trades.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-3 py-7 text-center text-on-surface-variant">
                    {loading ? 'Loading…' : 'No closed trades on this day.'}
                  </td>
                </tr>
              )}
              {trades.map((row) => {
                const expandable = row.charges !== null
                const expanded = open === row.trade_id
                return (
                  <Fragment key={row.trade_id}>
                    <tr
                      className={`border-b border-[#eef0f4] ${expandable ? 'cursor-pointer hover:!bg-surface-container-low' : ''}`}
                      onClick={() => expandable && setOpen(expanded ? null : row.trade_id)}
                      aria-expanded={expandable ? expanded : undefined}
                    >
                      <td className={`${CELL} font-data`}>{timeIst(row.closed_at)}</td>
                      <td className={CELL}>
                        <span className="inline-flex items-center gap-2">
                          <Side direction={row.direction} />
                          <span className="font-bold">{row.tradingsymbol}</span>
                        </span>
                      </td>
                      <td className={NUM}>{row.qty}</td>
                      <td className={NUM}>{num(row.entry_avg)}</td>
                      <td className={NUM}>{num(row.exit_avg)}</td>
                      <td className={`${NUM} ${pnlClass(row.gross_pnl)}`}>{inr(row.gross_pnl)}</td>
                      <td className={NUM}>{inr(row.charges?.total)}</td>
                      <td className={`${NUM} font-bold ${pnlClass(row.net_pnl)}`}>{inr(row.net_pnl)}</td>
                      <td className={CELL}>
                        <StatusCell row={row} />
                      </td>
                    </tr>
                    {expanded && row.charges && (
                      <tr className="border-b border-[#eef0f4] bg-surface-container-low/60">
                        <td colSpan={9} className="px-3 py-2.5">
                          <div className="flex flex-wrap gap-x-6 gap-y-1 text-[12px]">
                            {BREAKDOWN.map(([key, label]) => (
                              <span key={key}>
                                <span className="text-on-surface-variant">{label} </span>
                                <span className="font-data tabular-nums">{inr(row.charges?.[key])}</span>
                              </span>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  )
}

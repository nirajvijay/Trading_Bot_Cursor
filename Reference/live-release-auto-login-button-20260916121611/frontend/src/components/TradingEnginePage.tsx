import { useMemo, useState } from 'react'
import { formatTimeIst } from '../lib/format'
import { useTradingEngine } from '../hooks/useTradingEngine'
import type { TradingTradeRow } from '../api/types'

function fmt(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toLocaleString('en-IN', { maximumFractionDigits: digits, minimumFractionDigits: 0 })
}

function inr(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return `₹${fmt(n, 2)}`
}

function estIfHit(row: TradingTradeRow, stop: number): number | null {
  const entry = row.entry_fill ?? row.entry_estimate
  if (entry == null || !row.qty) return null
  if (row.direction === 'UP') return row.qty * (stop - entry)
  return row.qty * (entry - stop)
}

function statusLabel(state: string): string {
  if (state === 'critical') return 'CRITICAL'
  if (state === 'starting') return 'STARTING'
  if (state === 'stopping') return 'STOPPING'
  if (state === 'paused') return 'RUNNING · not taking new trades'
  if (state === 'running') return 'RUNNING'
  if (state === 'error') return 'ERROR'
  return 'STOPPED'
}

function statusClass(state: string): string {
  if (state === 'critical' || state === 'error') return 'bg-red-50 text-negative border-red-200'
  if (state === 'running') return 'bg-emerald-50 text-positive border-emerald-200'
  if (state === 'starting' || state === 'stopping' || state === 'paused') {
    return 'bg-amber-50 text-amber-800 border-amber-200'
  }
  return 'bg-surface-container text-on-surface-variant border-outline-variant'
}

function skipReasonLabel(reason: string | null | undefined): string {
  if (reason === 'vwap_limited') return 'VWAP LIMITED'
  if (reason === 'vwap_reject') return 'VWAP REJECT'
  if (reason === 'vwap_unavailable') return 'VWAP UNAVAILABLE'
  return reason || '—'
}

function tradeStatusLabel(status: string): string {
  if (status === 'entry_submitting') return 'Pending entry'
  if (status === 'entry_filled') return 'Entry filled'
  if (status === 'stop_pending') return 'Stop pending'
  if (status === 'protected_open') return 'Protected open'
  if (status === 'closed') return 'Closed'
  if (status === 'skipped') return 'Skipped'
  if (status === 'rejected') return 'Rejected'
  return status
}

function isUnprotected(row: TradingTradeRow): boolean {
  return row.status === 'entry_filled' || row.status === 'stop_pending'
}

function TrailControls({
  row,
  draft,
  busy,
  onDraft,
  onSet,
  onAuto,
}: {
  row: TradingTradeRow
  draft: string | undefined
  busy: boolean
  onDraft: (value: string) => void
  onSet: (value: number) => Promise<void>
  onAuto?: (enabled: boolean) => Promise<void>
}) {
  const tick = row.tick_size && row.tick_size > 0 ? row.tick_size : 0.05
  const current = row.current_stop ?? 0
  const display = draft !== undefined && draft !== '' ? draft : String(current)
  const numeric = Number(display)
  const usable = !Number.isNaN(numeric)
  const est = usable ? estIfHit(row, numeric) : null

  const bump = (delta: number) => {
    const base = usable ? numeric : current
    const next = Math.round((base + delta) / tick) * tick
    onDraft(String(Number(next.toFixed(4))))
  }

  return (
    <form
      className="flex flex-wrap items-center gap-1"
      onSubmit={(e) => {
        e.preventDefault()
        if (!usable) return
        void onSet(numeric)
      }}
    >
      <button
        type="button"
        className="px-1.5 py-0.5 border border-outline-variant bg-white font-data"
        disabled={busy}
        onClick={() => bump(-tick)}
      >
        −
      </button>
      <input
        type="number"
        step={tick}
        className="w-20 bg-surface-container-low border border-outline-variant font-data text-[10px] px-1 py-0.5"
        value={display}
        onChange={(e) => onDraft(e.target.value)}
      />
      <button
        type="button"
        className="px-1.5 py-0.5 border border-outline-variant bg-white font-data"
        disabled={busy}
        onClick={() => bump(tick)}
      >
        +
      </button>
      <button
        type="submit"
        disabled={busy || !usable}
        className="label-caps px-1.5 py-0.5 border border-outline-variant bg-white"
      >
        Set
      </button>
      {onAuto && (
        <label className="flex items-center gap-1 text-[10px] text-on-surface-variant">
          <input
            type="checkbox"
            disabled={busy}
            checked={Boolean(row.auto_trail_enabled)}
            onChange={(e) => void onAuto(e.target.checked)}
          />
          Auto
        </label>
      )}
      <span
        className={`font-data text-[10px] ${
          est != null && est < 0 ? 'text-negative' : 'text-positive'
        }`}
      >
        Est. if hit (stop) {inr(est)}
      </span>
      {row.auto_trail_enabled && row.auto_trail_ticks != null && (
        <span className="text-[10px] text-on-surface-variant">gap {row.auto_trail_ticks}t</span>
      )}
    </form>
  )
}

function TradeTable({
  rows,
  kind,
  onTrail,
  onAutoTrail,
}: {
  rows: TradingTradeRow[]
  kind: 'active' | 'closed' | 'skipped'
  onTrail?: (tradeId: string, newStop: number) => Promise<void>
  onAutoTrail?: (tradeId: string, enabled: boolean) => Promise<void>
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)

  return (
    <div className="overflow-auto custom-scrollbar border border-outline-variant">
      <table className="w-full text-left text-xs">
        <thead className="bg-surface-container-low label-caps text-on-surface-variant">
          <tr>
            <th className="px-2 py-1.5">Symbol</th>
            <th className="px-2 py-1.5">Dir</th>
            <th className="px-2 py-1.5 text-right">Qty</th>
            <th className="px-2 py-1.5 text-right">Entry</th>
            <th className="px-2 py-1.5 text-right">Initial stop</th>
            <th className="px-2 py-1.5 text-right">Current stop</th>
            <th className="px-2 py-1.5 text-right">Margin</th>
            <th className="px-2 py-1.5">Status</th>
            <th className="px-2 py-1.5">Trigger</th>
            {kind === 'active' && <th className="px-2 py-1.5 text-right">Downside risk</th>}
            {kind === 'active' && <th className="px-2 py-1.5 text-right">Open P&L</th>}
            {kind === 'closed' && <th className="px-2 py-1.5">Close reason</th>}
            {kind === 'closed' && <th className="px-2 py-1.5 text-right">Realised P&L</th>}
            {kind === 'skipped' && <th className="px-2 py-1.5">Reason</th>}
            {kind === 'active' && <th className="px-2 py-1.5">Trail stop</th>}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td className="px-2 py-3 text-on-surface-variant" colSpan={14}>
                None
              </td>
            </tr>
          )}
          {rows.map((row) => {
            const unprotected = isUnprotected(row)
            const stopChanged = row.stop_revised
            return (
              <tr
                key={row.trade_id}
                className={`terminal-row border-t border-outline-variant ${
                  unprotected ? 'bg-red-50' : ''
                }`}
              >
                <td className="px-2 py-1.5 font-data font-semibold">{row.symbol}</td>
                <td className="px-2 py-1.5">{row.direction === 'UP' ? 'BUY' : 'SELL'}</td>
                <td className="px-2 py-1.5 text-right font-data">{row.qty || '—'}</td>
                <td className="px-2 py-1.5 text-right font-data">
                  {fmt(row.entry_fill ?? row.entry_estimate)}
                </td>
                <td className="px-2 py-1.5 text-right font-data text-on-surface-variant">
                  {fmt(row.initial_stop)}
                </td>
                <td
                  className={`px-2 py-1.5 text-right font-data ${
                    stopChanged ? 'font-bold text-primary' : ''
                  }`}
                >
                  {fmt(row.current_stop)}
                  {stopChanged ? ' *' : ''}
                </td>
                <td className="px-2 py-1.5 text-right font-data">{inr(row.margin_blocked)}</td>
                <td className="px-2 py-1.5">
                  {tradeStatusLabel(row.status)}
                  {unprotected ? ' · UNPROTECTED' : ''}
                </td>
                <td className="px-2 py-1.5 font-data">
                  {row.trigger_time ? formatTimeIst(row.trigger_time) : '—'}
                </td>
                {kind === 'active' && (
                  <td className="px-2 py-1.5 text-right font-data">
                    {inr(row.remaining_downside_risk)}
                  </td>
                )}
                {kind === 'active' && (
                  <td
                    className={`px-2 py-1.5 text-right font-data ${
                      row.open_pnl < 0 ? 'text-negative' : 'text-positive'
                    }`}
                  >
                    {inr(row.open_pnl)}
                  </td>
                )}
                {kind === 'closed' && (
                  <td className="px-2 py-1.5">{row.close_reason || '—'}</td>
                )}
                {kind === 'closed' && (
                  <td
                    className={`px-2 py-1.5 text-right font-data ${
                      row.realised_pnl < 0 ? 'text-negative' : 'text-positive'
                    }`}
                  >
                    {inr(row.realised_pnl)}
                  </td>
                )}
                {kind === 'skipped' && (
                  <td className="px-2 py-1.5">
                    {skipReasonLabel(row.skip_reason || row.reject_reason)}
                  </td>
                )}
                {kind === 'active' && onTrail && (
                  <td className="px-2 py-1.5">
                    {row.status === 'protected_open' ? (
                      <TrailControls
                        row={row}
                        draft={drafts[row.trade_id]}
                        busy={busy === row.trade_id}
                        onDraft={(value) =>
                          setDrafts((d) => ({ ...d, [row.trade_id]: value }))
                        }
                        onSet={async (value) => {
                          setBusy(row.trade_id)
                          try {
                            await onTrail(row.trade_id, value)
                          } finally {
                            setBusy(null)
                          }
                        }}
                        onAuto={
                          onAutoTrail
                            ? async (enabled) => {
                                setBusy(row.trade_id)
                                try {
                                  await onAutoTrail(row.trade_id, enabled)
                                } finally {
                                  setBusy(null)
                                }
                              }
                            : undefined
                        }
                      />
                    ) : (
                      <span className="text-on-surface-variant">—</span>
                    )}
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function TradingEnginePage({ sessionDate }: { sessionDate: string }) {
  const engine = useTradingEngine(sessionDate, true)
  const snap = engine.snapshot
  const status = engine.status
  const stateRaw = String(status?.state || snap?.state || 'stopped')
  const [capitalDraft, setCapitalDraft] = useState('')
  const [confirmLive, setConfirmLive] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const totalCapital = status?.total_capital ?? snap?.total_capital ?? 300000
  const unprotected = (status?.unprotected_count ?? snap?.unprotected_count ?? 0) > 0
  const running = Boolean(status?.engine_running)
  const accepting = Boolean(status?.accepting_triggers ?? snap?.accepting_triggers)
  const liveOrders = Boolean(status?.live_orders_enabled || snap?.live_orders_enabled)
  const displayState = engine.stopping
    ? 'stopping'
    : engine.starting || stateRaw === 'starting'
      ? 'starting'
      : running && !accepting && stateRaw === 'running'
        ? 'paused'
        : stateRaw
  const capitalValue = useMemo(() => {
    const n = Number(capitalDraft)
    if (capitalDraft && !Number.isNaN(n) && n > 0) return n
    return totalCapital
  }, [capitalDraft, totalCapital])

  return (
    <div className="flex-1 min-h-0 overflow-auto custom-scrollbar bg-background">
      <div className="p-4 flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-extrabold uppercase tracking-tight">Trading Engine</h1>
            <span className={`label-caps font-extrabold px-2.5 py-1 border rounded ${statusClass(displayState)}`}>
              {statusLabel(displayState)}
            </span>
            <span className="text-[11px] text-on-surface-variant">
              {(status?.require_vwap_accept ?? snap?.require_vwap_accept) === false
                ? 'VWAP gate: OFF — filter bypassed'
                : 'VWAP gate: ON — ACCEPT only'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-[11px] text-on-surface-variant">
              <input
                type="checkbox"
                disabled={running || engine.starting}
                checked={confirmLive || Boolean(status?.live_orders_enabled || snap?.live_orders_enabled)}
                onChange={(e) => setConfirmLive(e.target.checked)}
              />
              Live Kite orders
            </label>
            {engine.starting ? (
              <button
                type="button"
                className="label-caps px-3 py-1.5 bg-primary text-white"
                disabled
              >
                Starting…
              </button>
            ) : running || engine.stopping ? (
              <button
                type="button"
                className="label-caps px-3 py-1.5 border border-outline-variant bg-white"
                disabled={engine.stopping}
                onClick={() => void engine.stop()}
              >
                {engine.stopping ? 'Stopping…' : 'Stop'}
              </button>
            ) : (
              <button
                type="button"
                className="label-caps px-3 py-1.5 bg-primary text-white"
                onClick={async () => {
                  setActionError(null)
                  try {
                    await engine.start(confirmLive, capitalValue)
                  } catch (err) {
                    setActionError(err instanceof Error ? err.message : 'Start failed')
                  }
                }}
              >
                Start
              </button>
            )}
          </div>
        </div>

        {unprotected && (
          <div className="px-3 py-2 bg-red-100 border-2 border-negative text-negative text-sm font-semibold">
            Unprotected position: a fill has no confirmed stop. New trades are blocked. ₹900 / ₹3,000
            limits are not in force until the broker stop is confirmed.
          </div>
        )}
        {(engine.error || actionError) && (
          <div className="px-3 py-2 bg-red-50 border border-red-200 text-red-800 text-sm">
            {actionError || engine.error}
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2">
          <label className="bg-surface-container-low border border-outline-variant px-2 py-2 flex flex-col gap-1">
            <span className="label-caps text-on-surface-variant">Total capital</span>
            <input
              className="font-data text-sm bg-transparent border-b border-outline-variant"
              value={capitalDraft || String(Math.round(totalCapital))}
              onChange={(e) => setCapitalDraft(e.target.value)}
              onBlur={() => {
                if (capitalValue > 0 && capitalValue !== totalCapital) {
                  void engine.setCapital(capitalValue)
                }
              }}
            />
          </label>
          <div className="bg-surface-container-low border border-outline-variant px-2 py-2">
            <div className="label-caps text-on-surface-variant">Live P&L</div>
            <div className="font-data text-sm">{inr(status?.live_pnl ?? snap?.live_pnl ?? 0)}</div>
          </div>
          <div className="bg-surface-container-low border border-outline-variant px-2 py-2">
            <div className="label-caps text-on-surface-variant">Remaining daily risk</div>
            <div className="font-data text-sm">{inr(status?.remaining_daily ?? 3000)}</div>
          </div>
          <div className="bg-surface-container-low border border-outline-variant px-2 py-2">
            <div className="label-caps text-on-surface-variant">Committed risk</div>
            <div className="font-data text-sm">{inr(status?.committed_risk ?? 0)}</div>
          </div>
          <div className="bg-surface-container-low border border-outline-variant px-2 py-2">
            <div className="label-caps text-on-surface-variant">
              {liveOrders ? 'Live mode' : 'Demo leverage'}
            </div>
            <div className="font-data text-sm">
              {liveOrders ? 'Live Kite' : `${status?.leverage_factor ?? snap?.leverage_factor ?? 5}x (demo)`}
            </div>
          </div>
          <div className="bg-surface-container-low border border-outline-variant px-2 py-2">
            <div className="label-caps text-on-surface-variant">Margin used</div>
            <div className="font-data text-sm">{inr(status?.margin_used ?? 0)}</div>
          </div>
          <div className="bg-surface-container-low border border-outline-variant px-2 py-2">
            <div className="label-caps text-on-surface-variant">Remaining capital</div>
            <div className="font-data text-sm">{inr(status?.remaining_capital ?? 0)}</div>
          </div>
          <div className="bg-surface-container-low border border-outline-variant px-2 py-2">
            <div className="label-caps text-on-surface-variant">Buying power</div>
            <div className="font-data text-sm">{inr(status?.buying_power ?? 0)}</div>
          </div>
        </div>
        <p className="text-[11px] text-on-surface-variant">
          5x is demo sizing only. Live mode verifies actual Kite MIS margin before entry. Stop does not
          flatten open positions.
        </p>

        <section>
          <h2 className="label-caps mb-1">Active — pending entry, stop pending, protected open</h2>
          <TradeTable
            kind="active"
            rows={snap?.active ?? []}
            onTrail={(id, stop) => engine.trailStop(id, stop)}
            onAutoTrail={(id, enabled) => engine.autoTrail(id, enabled)}
          />
        </section>
        <section>
          <h2 className="label-caps mb-1">Closed — broker-confirmed with realised P&L</h2>
          <TradeTable kind="closed" rows={snap?.closed ?? []} />
        </section>
        <section>
          <h2 className="label-caps mb-1">Skipped / Rejected</h2>
          <TradeTable kind="skipped" rows={snap?.skipped ?? []} />
        </section>
      </div>
    </div>
  )
}

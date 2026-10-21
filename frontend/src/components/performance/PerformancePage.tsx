import { useState } from 'react'
import { postPerformanceCheck } from '../../api/client'
import type { PerformanceGate, PerformanceSummary } from '../../api/types'
import { usePerformance } from '../../hooks/usePerformance'
import { bps, inr, pnlClass, rMultiple, reasonLabel, stageLabel, timeIst } from '../execution/format'

/**
 * Performance tab: ARB's forward test against its backtest.
 *
 * Display only. One stage at a time: rupees from different stages cannot be
 * added. Stages are judged in R before charges, because at micro size
 * charges are about twice as large in R as at full size.
 */

const TH = 'px-3 py-2 label-caps whitespace-nowrap border-b border-outline-variant'
const CELL = 'px-3 py-2'
const NUM = `${CELL} text-right font-data tabular-nums`

function pctText(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}

function Tile({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: string }) {
  return (
    <div className="bg-surface px-4 py-3 flex flex-col gap-1 min-w-0">
      <div className="label-caps text-on-surface-variant">{label}</div>
      <div className={`font-data tabular-nums text-[18px] font-medium ${tone ?? ''}`}>{value}</div>
      {note && <div className="text-[10px] text-on-surface-variant text-pretty">{note}</div>}
    </div>
  )
}

function GateRow({ gate }: { gate: PerformanceGate }) {
  const mark = gate.ok === null ? '·' : gate.ok ? '✓' : '✕'
  const tone = gate.ok === null ? 'bg-[#a3a8b3]' : gate.ok ? 'bg-positive' : 'bg-negative'
  return (
    <li className="flex items-start gap-2.5 px-4 py-2 border-b border-[#eef0f4] last:border-b-0 text-[12px]">
      <span
        className={`shrink-0 mt-px size-[16px] rounded-full text-white flex items-center justify-center text-[10px] font-extrabold ${tone}`}
      >
        {mark}
      </span>
      <span className="min-w-0">
        <span className="font-semibold">{gate.label}</span>
        <span className="block text-on-surface-variant text-pretty">{gate.detail}</span>
      </span>
      <span className="ml-auto label-caps shrink-0 text-on-surface-variant">
        {gate.ok === null ? 'Not judged yet' : gate.ok ? 'Met' : 'Not met'}
      </span>
    </li>
  )
}

function StagePanel({ data }: { data: PerformanceSummary }) {
  const { stage } = data
  return (
    <section className="bg-surface border border-outline-variant rounded-md overflow-hidden">
      <div className="px-4 py-2.5 border-b border-outline-variant flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className="text-[13px] font-extrabold uppercase tracking-tight">
          Stage: {stageLabel(stage.risk_scale)}
        </h2>
        <span className="text-[12px] text-on-surface-variant">
          {stage.days} trading day{stage.days === 1 ? '' : 's'}, {stage.trades} filled trade
          {stage.trades === 1 ? '' : 's'}
          {stage.first_day ? ` since ${stage.first_day}` : ''}
        </span>
        {stage.next_scale !== null && (
          <span
            className={`ml-auto label-caps px-2 py-[3px] rounded border ${
              stage.all_gates_met
                ? 'bg-emerald-50 text-positive border-emerald-200'
                : 'bg-surface-container-low text-on-surface-variant border-outline-variant'
            }`}
          >
            {stage.all_gates_met
              ? `Every gate for ${stageLabel(stage.next_scale)} is met`
              : `Gates for ${stageLabel(stage.next_scale)} not all met`}
          </span>
        )}
      </div>
      {stage.gates.length > 0 ? (
        <ul>
          {stage.gates.map((gate) => (
            <GateRow key={gate.key} gate={gate} />
          ))}
        </ul>
      ) : (
        <p className="px-4 py-3 text-[12px] text-on-surface-variant">This is the full size; there is no next stage.</p>
      )}
      {stage.next_scale !== null && (
        <p className="px-4 py-2.5 border-t border-outline-variant text-[11px] text-on-surface-variant text-pretty">
          Meeting the gates does not raise the size by itself. The largest stage that can be
          started is {stageLabel(stage.unlocked_scale)}; raising it is a code change, made after
          these gates pass.
        </p>
      )}
    </section>
  )
}

function todayIst(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date())
}

/** Runs the post-market check for one day and stores it. */
function CheckRunner({ onDone }: { onDone: (day: string) => void }) {
  const [day, setDay] = useState(todayIst)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  return (
    <div className="px-4 py-2.5 border-t border-outline-variant flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <span className="label-caps text-on-surface-variant">Check a day</span>
        <input
          type="date"
          value={day}
          max={todayIst()}
          onChange={(e) => setDay(e.target.value)}
          className="font-data text-[12px] border border-[#d6dbe4] rounded px-2 py-1 bg-surface"
        />
        <button
          type="button"
          disabled={busy || !day}
          className="label-caps px-3 py-[6px] rounded bg-primary text-white disabled:bg-surface-container disabled:text-on-surface-variant"
          onClick={async () => {
            setBusy(true)
            setMessage(null)
            try {
              const done = await postPerformanceCheck(day)
              setMessage({ ok: true, text: done.report })
              onDone(day)
            } catch (err) {
              setMessage({ ok: false, text: err instanceof Error ? err.message : 'Check failed' })
            } finally {
              setBusy(false)
            }
          }}
        >
          {busy ? 'Checking…' : 'Run check'}
        </button>
        <span className="text-[11px] text-on-surface-variant">
          Replays the day on its own bars with the same rules. Run it after the close. It reads
          only; no order is placed.
        </span>
      </div>
      {message && (
        <pre
          className={`text-[11px] font-data whitespace-pre-wrap px-3 py-2 rounded border ${
            message.ok
              ? 'bg-surface-container-low border-outline-variant'
              : 'bg-red-50 border-red-200 text-negative'
          }`}
        >
          {message.text}
        </pre>
      )}
    </div>
  )
}

function seconds(value: number | null | undefined): string {
  return value == null ? '—' : `${value.toFixed(1)} s`
}

/** Where the entry slippage came from, per source and per recent trade. Display only. */
function SlippageDetail({ slippage }: { slippage: PerformanceSummary['slippage'] }) {
  const sources = slippage.by_source ?? []
  const recent = slippage.recent ?? []
  if (!sources.length) return null
  return (
    <div className="border-t border-outline-variant">
      <div className="overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead className="bg-surface-container-low text-on-surface-variant">
            <tr>
              <th className={`${TH} text-left`}>Entry slippage</th>
              <th className={`${TH} text-right`}>Trades</th>
              <th className={`${TH} text-right`}>Median</th>
              <th className={`${TH} text-right`}>Mean</th>
              <th className={`${TH} text-right`}>Worst</th>
              <th className={`${TH} text-right`}>Already at signal</th>
              <th className={`${TH} text-right`}>After signal</th>
              <th className={`${TH} text-right`}>Cost</th>
              <th className={`${TH} text-right`}>Signal → engine</th>
              <th className={`${TH} text-right`}>Engine → fill</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((row) => (
              <tr key={row.source} className="border-b border-[#eef0f4] last:border-b-0">
                <td className={`${CELL} font-bold`}>{row.source}</td>
                <td className={NUM}>{row.trades}</td>
                <td className={NUM}>{bps(row.median)}</td>
                <td className={NUM}>{bps(row.mean)}</td>
                <td className={NUM}>{bps(row.worst)}</td>
                <td className={NUM}>{bps(row.at_signal_mean)}</td>
                <td className={NUM}>{bps(row.after_signal_mean)}</td>
                <td className={NUM}>{inr(row.rupees, 0)}</td>
                <td className={NUM}>{seconds(row.signal_to_engine_s)}</td>
                <td className={NUM}>{seconds(row.engine_to_fill_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {recent.length > 0 && (
        <details className="border-t border-outline-variant">
          <summary className="px-4 py-2 text-[12px] font-bold cursor-pointer">
            Recent trades ({recent.length}) · total cost {inr(slippage.entry_rupees ?? 0, 0)}
          </summary>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead className="bg-surface-container-low text-on-surface-variant">
                <tr>
                  <th className={`${TH} text-left`}>Day</th>
                  <th className={`${TH} text-left`}>Stock</th>
                  <th className={`${TH} text-left`}>Source</th>
                  <th className={`${TH} text-right`}>Shares</th>
                  <th className={`${TH} text-right`}>Slippage</th>
                  <th className={`${TH} text-right`}>At signal</th>
                  <th className={`${TH} text-right`}>After</th>
                  <th className={`${TH} text-right`}>Cost</th>
                  <th className={`${TH} text-right`}>Signal → engine</th>
                  <th className={`${TH} text-right`}>Engine → fill</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((row, i) => (
                  <tr key={`${row.session_date}-${row.symbol}-${i}`} className="border-b border-[#eef0f4] last:border-b-0">
                    <td className={CELL}>{row.session_date}</td>
                    <td className={`${CELL} font-bold`}>{row.symbol}</td>
                    <td className={CELL}>{row.source ?? '—'}</td>
                    <td className={NUM}>
                      {row.qty}
                      {row.trigger_based_qty != null && row.trigger_based_qty !== row.qty && (
                        <span className="text-on-surface-variant"> (was {row.trigger_based_qty})</span>
                      )}
                    </td>
                    <td className={NUM}>{bps(row.bps)}</td>
                    <td className={NUM}>{bps(row.at_signal)}</td>
                    <td className={NUM}>{bps(row.after_signal)}</td>
                    <td className={NUM}>{row.rupees == null ? '—' : inr(row.rupees, 0)}</td>
                    <td className={NUM}>{seconds(row.signal_to_engine_s)}</td>
                    <td className={NUM}>{seconds(row.engine_to_fill_s)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      <p className="px-4 py-2.5 text-[11px] text-on-surface-variant text-pretty">
        &ldquo;Already at signal&rdquo; is how far past the signal price the last trade was when the signal fired;
        &ldquo;after signal&rdquo; is the rest, up to Kite&rsquo;s fill. Since 22 Oct shares are worked out from the
        worse of the trigger and that last price (funnel plus 3 bps), so &ldquo;was&rdquo; shows the old size.
      </p>
    </div>
  )
}

export function PerformancePage() {
  const [parityDate, setParityDate] = useState<string | null>(null)
  const { data, loading, error, refresh } = usePerformance(parityDate)

  if (!data) {
    return (
      <div className="flex-1 min-h-0 overflow-y-auto bg-background p-6 text-[12px] text-on-surface-variant">
        {error ? <span className="text-negative">{error}</span> : 'Loading…'}
      </div>
    )
  }
  const { totals, slippage, parity, reference } = data
  const scale = data.risk_scale
  const ref3 = reference.at_3bps
  const ref5 = reference.at_5bps
  const empty = totals.trades === 0

  return (
    <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar bg-background">
      <div className="max-w-[1600px] mx-auto px-6 pt-5 pb-6 flex flex-col gap-3.5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h1 className="text-[18px] font-extrabold uppercase tracking-tight">Performance</h1>
            <p className="text-[12px] text-on-surface-variant">
              ARB live at {stageLabel(scale)}, against its backtest. Charges are the engine&apos;s
              estimate; the Charges tab has Kite&apos;s own.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            className="label-caps px-3 py-[7px] rounded border border-outline-variant bg-surface hover:bg-surface-container-low disabled:opacity-50"
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        {error && (
          <div className="px-3 py-2 bg-red-50 border border-red-200 text-red-800 text-[12px]">{error}</div>
        )}
        {empty && (
          <div className="px-3.5 py-2.5 bg-surface border border-outline-variant rounded-md text-[12px] text-on-surface-variant">
            No ARB trade has closed yet. Start a session in ARB mode on the Execution Desk; the
            numbers below fill in from the first closed trade.
          </div>
        )}

        <StagePanel data={data} />

        <section className="grid grid-cols-2 lg:grid-cols-5 gap-px bg-outline-variant border border-outline-variant rounded-md overflow-hidden">
          <Tile
            label="Net after charges"
            value={inr(totals.net, 0)}
            tone={pnlClass(totals.net)}
            note={`Gross ${inr(totals.gross, 0)} · charges ${inr(totals.charges, 0)}`}
          />
          <Tile
            label="R before charges"
            value={rMultiple(totals.r_gross)}
            tone={pnlClass(totals.r_gross)}
            note={`${totals.trades} trades · ${pctText(totals.win_rate)} profitable after charges`}
          />
          <Tile
            label="Green days"
            value={`${totals.green_days} of ${totals.days}`}
            note={`Backtest: ${pctText(ref3.green_days)} of days`}
          />
          <Tile
            label="Worst day"
            value={inr(totals.worst_day, 0)}
            tone={pnlClass(totals.worst_day)}
            note={`Backtest at this size: ${inr(ref3.worst_day * scale, 0)}`}
          />
          <Tile
            label="Max drawdown"
            value={inr(totals.max_drawdown, 0)}
            tone={pnlClass(totals.max_drawdown)}
            note={`Backtest at this size: ${inr(ref3.max_drawdown * scale, 0)}`}
          />
        </section>
        <p className="text-[11px] text-on-surface-variant text-pretty -mt-1.5">
          Backtest of the rule that is live ({reference.period}, full size, after charges):{' '}
          {inr(ref3.net_per_month, 0)} a month with 3 bps of slippage, {inr(ref5.net_per_month, 0)}{' '}
          with 5 bps. At {stageLabel(scale)} that is about {inr(ref3.net_per_month * scale, 0)} a
          month before the larger share that charges take at small size.
        </p>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3.5 items-start">
          <section className="bg-surface border border-outline-variant rounded-md overflow-x-auto">
            <h2 className="px-4 py-2.5 border-b border-outline-variant text-[13px] font-extrabold uppercase tracking-tight">
              By source
            </h2>
            <table className="w-full text-[12px]">
              <thead className="bg-surface-container-low text-on-surface-variant">
                <tr>
                  <th className={`${TH} text-left`}>Source</th>
                  <th className={`${TH} text-right`}>Trades</th>
                  <th className={`${TH} text-right`}>Profitable</th>
                  <th className={`${TH} text-right`}>Net</th>
                  <th className={`${TH} text-right`}>R total</th>
                  <th className={`${TH} text-right`}>R average</th>
                </tr>
              </thead>
              <tbody>
                {data.sources.map((row) => (
                  <tr key={row.source} className="border-b border-[#eef0f4] last:border-b-0">
                    <td className={`${CELL} font-bold`}>{row.source}</td>
                    <td className={NUM}>{row.trades}</td>
                    <td className={NUM}>{row.trades ? `${row.wins} of ${row.trades}` : '—'}</td>
                    <td className={`${NUM} ${pnlClass(row.net)}`}>{row.trades ? inr(row.net, 0) : '—'}</td>
                    <td className={`${NUM} ${pnlClass(row.r_gross)}`}>{row.trades ? rMultiple(row.r_gross) : '—'}</td>
                    <td className={NUM}>{rMultiple(row.avg_r)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="bg-surface border border-outline-variant rounded-md">
            <h2 className="px-4 py-2.5 border-b border-outline-variant text-[13px] font-extrabold uppercase tracking-tight">
              Slippage
            </h2>
            <div className="grid grid-cols-3 gap-px bg-outline-variant">
              <Tile
                label="Entry, median"
                value={bps(slippage.entry.median)}
                note={`Mean ${bps(slippage.entry.mean)} over ${slippage.entry.trades} trades`}
              />
              <Tile
                label={`Entry, last ${slippage.rolling_trades}`}
                value={bps(slippage.rolling_entry_mean)}
                tone={slippage.rolling_demote ? 'text-negative' : slippage.rolling_alert ? 'text-amber-700' : undefined}
                note={
                  slippage.rolling_entry_mean === null
                    ? `Needs ${slippage.rolling_trades} trades`
                    : slippage.rolling_demote
                      ? `Above ${slippage.demote_bps} bps: drop one stage`
                      : slippage.rolling_alert
                        ? `Above ${slippage.alert_bps} bps: watch it`
                        : `Alert above ${slippage.alert_bps} bps`
                }
              />
              <Tile
                label="Stop exits, median"
                value={bps(slippage.stop_exit.median)}
                note={`Mean ${bps(slippage.stop_exit.mean)} over ${slippage.stop_exit.trades} stops`}
              />
            </div>
            <p className="px-4 py-2.5 text-[11px] text-on-surface-variant text-pretty">
              Measured against the price the backtest assumed: the trigger price for a funnel
              trade, the opening range level for ORB. Positive is worse. The backtest earns{' '}
              {inr(ref3.net_per_month, 0)} a month at 3 bps and {inr(ref5.net_per_month, 0)} at 5.
            </p>
            <SlippageDetail slippage={slippage} />
          </section>
        </div>

        <section className="bg-surface border border-outline-variant rounded-md overflow-x-auto">
          <div className="px-4 py-2.5 border-b border-outline-variant flex flex-wrap items-baseline gap-x-3">
            <h2 className="text-[13px] font-extrabold uppercase tracking-tight">Parity with the replay</h2>
            <span className="text-[12px] text-on-surface-variant">
              {parity.match_rate === null
                ? 'No daily check stored yet. Run one below after the close.'
                : `${parity.matched} of ${parity.candidates} signals decided the same way (${pctText(parity.match_rate)}) · live ${rMultiple(parity.live_r)}, replay ${rMultiple(parity.replay_r)}`}
            </span>
          </div>
          {parity.unchecked_days.length > 0 && (
            <p className="px-4 py-2 text-[11px] text-amber-800 bg-amber-50 border-b border-amber-200">
              Traded but not checked: {parity.unchecked_days.join(', ')}
            </p>
          )}
          {parity.days.length > 0 && (
            <table className="w-full text-[12px]">
              <thead className="bg-surface-container-low text-on-surface-variant">
                <tr>
                  <th className={`${TH} text-left`}>Day</th>
                  <th className={`${TH} text-right`}>Signals</th>
                  <th className={`${TH} text-right`}>Same decision</th>
                  <th className={`${TH} text-right`}>Entered live / replay</th>
                  <th className={`${TH} text-right`}>R live</th>
                  <th className={`${TH} text-right`}>R replay</th>
                  <th className={`${TH} text-left`}>Day halt</th>
                  <th className={TH} />
                </tr>
              </thead>
              <tbody>
                {parity.days.map((day) => (
                  <tr key={day.session_date} className="border-b border-[#eef0f4] last:border-b-0">
                    <td className={`${CELL} font-data`}>{day.session_date}</td>
                    <td className={NUM}>{day.candidates ?? '—'}</td>
                    <td className={`${NUM} ${day.match_rate !== null && day.match_rate < 0.95 ? 'text-negative font-bold' : ''}`}>
                      {day.matched ?? '—'} ({pctText(day.match_rate)})
                    </td>
                    <td className={NUM}>
                      {day.live_entered ?? '—'} / {day.replay_entered ?? '—'}
                    </td>
                    <td className={`${NUM} ${pnlClass(day.live_r)}`}>{rMultiple(day.live_r)}</td>
                    <td className={`${NUM} ${pnlClass(day.replay_r)}`}>{rMultiple(day.replay_r)}</td>
                    <td className={`${CELL} ${day.halt_match === false ? 'text-negative font-bold' : ''}`}>
                      {day.live_halt ? reasonLabel(day.live_halt.reason) : 'none'}
                      {day.halt_match === false &&
                        ` (replay: ${day.replay_halt ? reasonLabel(day.replay_halt.reason) : 'none'})`}
                    </td>
                    <td className={`${CELL} text-right`}>
                      <button
                        type="button"
                        className="label-caps text-primary"
                        onClick={() =>
                          setParityDate(parityDate === day.session_date ? null : day.session_date)
                        }
                      >
                        {parityDate === day.session_date ? 'Hide' : 'Mismatches'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <CheckRunner
            onDone={(day) => {
              if (parityDate === day) void refresh()
              else setParityDate(day)
            }}
          />
          {parity.detail_date && (
            <div className="border-t border-outline-variant">
              <p className="px-4 py-2 label-caps text-on-surface-variant">
                {parity.detail_date}: {parity.detail_rows.length === 0 ? 'no mismatches' : 'mismatched signals'}
              </p>
              {parity.detail_rows.map((row) => (
                <p key={row.setup_id} className="px-4 py-1.5 border-t border-[#eef0f4] text-[12px]">
                  <span className="font-data">{timeIst(row.time)}</span>{' '}
                  <span className="font-bold">{row.symbol}</span> {row.strategy}:{' '}
                  {reasonLabel(row.why)}. Live {row.live.decision.replace('_', ' ')}
                  {row.live.reason ? ` (${reasonLabel(row.live.reason)})` : ''}
                  {row.live.risk_used != null ? ` at ${inr(row.live.risk_used, 0)}` : ''}; replay{' '}
                  {row.replay.decision}
                  {row.replay.reason ? ` (${reasonLabel(row.replay.reason)})` : ''}
                  {row.replay.risk_used != null ? ` at ${inr(row.replay.risk_used, 0)}` : ''}.
                </p>
              ))}
            </div>
          )}
        </section>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3.5 items-start">
          <section className="bg-surface border border-outline-variant rounded-md overflow-x-auto">
            <h2 className="px-4 py-2.5 border-b border-outline-variant text-[13px] font-extrabold uppercase tracking-tight">
              Days
            </h2>
            <table className="w-full text-[12px]">
              <thead className="bg-surface-container-low text-on-surface-variant">
                <tr>
                  <th className={`${TH} text-left`}>Day</th>
                  <th className={`${TH} text-right`}>Trades</th>
                  <th className={`${TH} text-right`}>Net</th>
                  <th className={`${TH} text-right`}>R</th>
                  <th className={`${TH} text-right`}>Running net</th>
                  <th className={`${TH} text-left`}>Stopped by</th>
                </tr>
              </thead>
              <tbody>
                {data.days.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-3 py-6 text-center text-on-surface-variant">
                      No closed ARB trades yet.
                    </td>
                  </tr>
                )}
                {[...data.days].reverse().map((day) => (
                  <tr key={day.session_date} className="border-b border-[#eef0f4] last:border-b-0">
                    <td className={`${CELL} font-data`}>{day.session_date}</td>
                    <td className={NUM}>{day.trades}</td>
                    <td className={`${NUM} font-semibold ${pnlClass(day.net)}`}>{inr(day.net, 0)}</td>
                    <td className={`${NUM} ${pnlClass(day.r_gross)}`}>{rMultiple(day.r_gross)}</td>
                    <td className={`${NUM} ${pnlClass(day.cumulative_net)}`}>{inr(day.cumulative_net, 0)}</td>
                    <td className={CELL}>{day.halted_reason ? reasonLabel(day.halted_reason) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <div className="flex flex-col gap-3.5">
            <section className="bg-surface border border-outline-variant rounded-md overflow-x-auto">
              <h2 className="px-4 py-2.5 border-b border-outline-variant text-[13px] font-extrabold uppercase tracking-tight">
                Months
              </h2>
              <table className="w-full text-[12px]">
                <thead className="bg-surface-container-low text-on-surface-variant">
                  <tr>
                    <th className={`${TH} text-left`}>Month</th>
                    <th className={`${TH} text-right`}>Net</th>
                    <th className={`${TH} text-right`}>R</th>
                    <th className={`${TH} text-right`}>Trades</th>
                    <th className={`${TH} text-right`}>Green days</th>
                  </tr>
                </thead>
                <tbody>
                  {data.months.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-3 py-6 text-center text-on-surface-variant">
                        Nothing yet.
                      </td>
                    </tr>
                  )}
                  {data.months.map((month) => (
                    <tr key={month.month} className="border-b border-[#eef0f4] last:border-b-0">
                      <td className={`${CELL} font-data`}>{month.month}</td>
                      <td className={`${NUM} font-semibold ${pnlClass(month.net)}`}>{inr(month.net, 0)}</td>
                      <td className={`${NUM} ${pnlClass(month.r_gross)}`}>{rMultiple(month.r_gross)}</td>
                      <td className={NUM}>{month.trades}</td>
                      <td className={NUM}>
                        {month.green_days} of {month.days}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <section className="bg-surface border border-outline-variant rounded-md overflow-x-auto">
              <h2 className="px-4 py-2.5 border-b border-outline-variant text-[13px] font-extrabold uppercase tracking-tight">
                Day controls that fired
              </h2>
              {data.day_controls.length === 0 ? (
                <p className="px-4 py-3 text-[12px] text-on-surface-variant">
                  Neither the hard day stop nor the day lock has fired at this stage.
                </p>
              ) : (
                <table className="w-full text-[12px]">
                  <thead className="bg-surface-container-low text-on-surface-variant">
                    <tr>
                      <th className={`${TH} text-left`}>Day</th>
                      <th className={`${TH} text-left`}>Control</th>
                      <th className={`${TH} text-left`}>At</th>
                      <th className={`${TH} text-right`}>Day P&L then</th>
                      <th className={`${TH} text-right`}>Peak</th>
                      <th className={`${TH} text-right`}>Day net</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.day_controls.map((row) => (
                      <tr key={row.session_date} className="border-b border-[#eef0f4] last:border-b-0">
                        <td className={`${CELL} font-data`}>{row.session_date}</td>
                        <td className={CELL}>{reasonLabel(row.reason)}</td>
                        <td className={`${CELL} font-data`}>{timeIst(row.at)}</td>
                        <td className={`${NUM} ${pnlClass(row.mtm)}`}>{inr(row.mtm, 0)}</td>
                        <td className={NUM}>{inr(row.peak, 0)}</td>
                        <td className={`${NUM} ${pnlClass(row.day_net)}`}>{inr(row.day_net, 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </div>
        </div>
      </div>
    </div>
  )
}

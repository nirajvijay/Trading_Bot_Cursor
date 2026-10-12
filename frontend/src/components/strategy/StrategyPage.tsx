import { useEffect, useState } from 'react'
import type { FunnelTrigger, OrbArm, OrbDay, OrbRankedStock } from '../../api/types'
import { useStrategy } from '../../hooks/useStrategy'
import { Side } from '../execution/PositionTables'
import { inr, timeIst } from '../execution/format'

/**
 * Strategy tab: what the signal side sees today.
 *
 * Display only. The ORB board shows the 09:20 ranking and each armed stock's
 * outcome; the funnel feed runs every continuation trigger through ARB's
 * filters and score. Nothing here places or changes an order.
 */

const DETECTOR_REASONS: Record<string, string> = {
  no_stocks_in_play: 'No stock reached 3× its usual opening volume',
  no_valid_ranges: 'No usable opening ranges. Was observation started after 09:15?',
  ranked_too_late: 'The feed was down across 09:20, so nothing was armed',
  ranking_write_failed: 'The ranking could not be saved, so nothing was armed',
  ranking_failed: 'The ranking failed, so nothing was armed',
  ranking_conflict: 'Another ranking already existed for today',
  restore_failed: 'Saved state could not be read back',
  all_arms_decided: 'Every armed stock has its outcome',
  too_many_tick_failures: 'Switched off after repeated errors',
}

const RANGE_REASONS: Record<string, string> = {
  no_baseline: 'no baseline',
  no_preopen_volume: 'no pre-open volume',
  no_range_ticks: 'no ticks in the range',
  range_ticks_stale: 'stale ticks before 09:20',
  bad_day_ohlc: 'bad day OHLC',
  volume_went_backwards: 'volume went backwards',
  no_range_volume: 'no volume in the range',
  no_direction: 'no direction',
  level_off_tick_grid: 'level off the tick grid',
  no_tick_size: 'no tick size',
}

const ARM_REASONS: Record<string, string> = {
  overshoot_beyond_limit: 'Already far beyond the level when first seen',
  no_valid_stop: 'No valid stop price',
  not_triggered_by_cutoff: 'No breakout by 11:00',
}

const FUNNEL_REASONS: Record<string, string> = {
  arb_volume_below_min: 'Volume under 2×',
  arb_after_funnel_cutoff: 'After 13:00',
  arb_stop_below_min_pct: 'Stop under 0.25%',
  arb_not_first_trigger_today: 'Not the first trigger today',
  arb_no_structural_stop: 'No structural stop',
  arb_no_trigger_time: 'No trigger time',
  context_unavailable: 'Market context unavailable',
}

const SCORE_PARTS: [string, string][] = [
  ['market_against', 'Market against'],
  ['sector_with', 'Sector with'],
  ['volume_6x', 'Volume 6×'],
  ['before_1030', 'Before 10:30'],
]

function label(map: Record<string, string>, key: string | null | undefined): string {
  if (!key) return '—'
  return map[key] ?? key.replace(/_/g, ' ')
}

function todayIstDate(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date())
}

function pct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`
}

function price(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function times(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(2)}×`
}

function detectorHeadline(orb: OrbDay | null): { value: string; note: string; tone: string } {
  const status = orb?.status
  if (!orb || !status) {
    return {
      value: 'Not started',
      note: 'The detector starts with observation',
      tone: 'text-on-surface-variant',
    }
  }
  if (status.state === 'collecting') {
    return { value: 'Waiting for 09:20', note: 'Building opening ranges', tone: 'text-on-surface' }
  }
  if (status.state === 'ranked') {
    const armed = orb.arms.filter((a) => a.state === 'ARMED').length
    return {
      value: `Watching ${armed} of ${orb.arms.length}`,
      note: `Breakouts count until ${orb.rules.trigger_end === '11:01' ? '11:00' : orb.rules.trigger_end}`,
      tone: 'text-positive',
    }
  }
  if (status.state === 'done') {
    return { value: 'Done for today', note: label(DETECTOR_REASONS, status.reason), tone: 'text-on-surface' }
  }
  return { value: 'Off', note: label(DETECTOR_REASONS, status.reason), tone: 'text-negative' }
}

function Tile({ label: name, value, note, tone = 'text-on-surface' }: {
  label: string
  value: string
  note?: string
  tone?: string
}) {
  return (
    <div className="bg-surface px-4 py-3 min-w-0">
      <div className="label-caps text-on-surface-variant">{name}</div>
      <div className={`font-data tabular-nums text-[18px] font-bold ${tone}`}>{value}</div>
      {note && <div className="text-[11px] text-on-surface-variant mt-0.5">{note}</div>}
    </div>
  )
}

const ARM_TONES: Record<string, string> = {
  ARMED: 'text-[#005db7] border-[#c9defa] bg-[#eff4ff]',
  TRIGGERED: 'text-positive border-emerald-200 bg-emerald-50',
  MISSED: 'text-amber-800 border-amber-200 bg-amber-50',
  EXPIRED: 'text-on-surface-variant border-outline-variant bg-surface-container-low',
}

function ArmState({ arm }: { arm: OrbArm }) {
  const text = arm.state.charAt(0) + arm.state.slice(1).toLowerCase()
  return (
    <span
      className={`label-caps inline-block px-2 py-0.5 rounded border ${ARM_TONES[arm.state] ?? ARM_TONES.EXPIRED}`}
      title={arm.reason ? label(ARM_REASONS, arm.reason) : undefined}
    >
      {text}
    </span>
  )
}

const CELL = 'px-3 py-2.5'
const NUM = `${CELL} text-right font-data tabular-nums whitespace-nowrap`
const TH = 'px-3 py-2 label-caps whitespace-nowrap border-b border-outline-variant'

function ArmsTable({ orb, loading }: { orb: OrbDay | null; loading: boolean }) {
  const arms = orb?.arms ?? []
  const empty = loading && !orb
    ? 'Loading…'
    : !orb?.ranked
      ? 'Stocks are ranked at 09:20. Nothing to show yet.'
      : 'No stock was armed today.'
  return (
    <section className="bg-surface border border-outline-variant rounded-md overflow-x-auto">
      <table className="w-full text-[12px] min-w-[980px]">
        <thead className="bg-surface-container-low text-on-surface-variant">
          <tr>
            <th className={`${TH} text-right`}>#</th>
            <th className={`${TH} text-left`}>Symbol</th>
            <th className={`${TH} text-right`}>RVOL</th>
            <th className={`${TH} text-right`}>Range low – high</th>
            <th className={`${TH} text-right`}>Level</th>
            <th className={`${TH} text-right`}>Stop distance</th>
            <th className={`${TH} text-left`}>State</th>
            <th className={`${TH} text-left`}>Time</th>
            <th className={`${TH} text-right`} title="The price when the breakout was first seen">
              Breakout price
            </th>
            <th className={`${TH} text-right`}>Stop</th>
            <th className={`${TH} text-right`}>Past level</th>
          </tr>
        </thead>
        <tbody>
          {arms.length === 0 && (
            <tr>
              <td colSpan={11} className="px-3 py-7 text-center text-on-surface-variant">
                {empty}
              </td>
            </tr>
          )}
          {arms.map((arm) => (
            <tr key={arm.setup_id} className="border-b border-[#eef0f4]">
              <td className={NUM}>{arm.rank}</td>
              <td className={CELL}>
                <span className="inline-flex items-center gap-2">
                  <Side direction={arm.direction} />
                  <span className="font-bold">{arm.symbol}</span>
                </span>
              </td>
              <td className={`${NUM} font-bold`}>{times(arm.rvol)}</td>
              <td className={NUM}>
                {price(arm.or_low)} – {price(arm.or_high)}
              </td>
              <td className={`${NUM} font-bold`}>{price(arm.level)}</td>
              <td className={NUM} title={`${orb?.rules.stop_atr_mult ?? 0.35} × ATR14 (${price(arm.atr14)})`}>
                {price(arm.stop_distance)}
              </td>
              <td className={CELL}>
                <ArmState arm={arm} />
              </td>
              <td className={`${CELL} font-data`}>{timeIst(arm.decided_at)}</td>
              <td className={NUM}>{arm.state === 'EXPIRED' ? '—' : price(arm.trigger_price)}</td>
              <td className={NUM}>
                {arm.planned_stop !== null ? (
                  price(arm.planned_stop)
                ) : arm.state === 'ARMED' ? (
                  <span className="text-on-surface-variant" title="If the breakout fires exactly at the level">
                    ≈ {price(arm.stop_at_level)}
                  </span>
                ) : (
                  '—'
                )}
              </td>
              <td className={NUM}>
                {arm.overshoot_bps === null ? '—' : `${arm.overshoot_bps.toFixed(1)} bps`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function StockChips({ title, stocks, note }: { title: string; stocks: OrbRankedStock[]; note: string }) {
  if (stocks.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[12px]">
      <span className="label-caps text-on-surface-variant" title={note}>
        {title}
      </span>
      {stocks.map((s) => (
        <span
          key={s.symbol}
          className="inline-flex items-center gap-1.5 px-2 py-0.5 border border-outline-variant bg-surface rounded"
        >
          <span className="font-bold">{s.symbol}</span>
          <span className="font-data tabular-nums text-on-surface-variant">{times(s.rvol)}</span>
          {s.direction && (
            <span className={s.direction === 'UP' ? 'text-positive' : 'text-negative'}>
              {s.direction === 'UP' ? '▲' : '▼'}
            </span>
          )}
        </span>
      ))}
    </div>
  )
}

function ScoreCell({ row }: { row: FunnelTrigger }) {
  if (row.score === null) return <span className="text-on-surface-variant">—</span>
  const met = SCORE_PARTS.filter(([key]) => row.score_parts[key]).map(([, text]) => text)
  return (
    <span title={met.length ? met.join(' · ') : 'No score condition met'}>
      <span className="font-data tabular-nums font-bold">{row.score}</span>
      <span className="ml-1.5 tracking-[2px]" aria-hidden="true">
        {SCORE_PARTS.map(([key]) => (
          <span key={key} className={row.score_parts[key] ? 'text-positive' : 'text-outline-variant'}>
            ●
          </span>
        ))}
      </span>
    </span>
  )
}

function FunnelTable({ rows, pending, loading }: { rows: FunnelTrigger[]; pending: number; loading: boolean }) {
  return (
    <section className="bg-surface border border-outline-variant rounded-md overflow-x-auto">
      <table className="w-full text-[12px] min-w-[980px]">
        <thead className="bg-surface-container-low text-on-surface-variant">
          <tr>
            <th className={`${TH} text-left`}>Time</th>
            <th className={`${TH} text-left`}>Symbol</th>
            <th className={`${TH} text-right`}>Volume</th>
            <th className={`${TH} text-right`}>Stop</th>
            <th className={`${TH} text-left`}>First today</th>
            <th className={`${TH} text-right`}>Market</th>
            <th className={`${TH} text-right`}>Sector</th>
            <th className={`${TH} text-left`}>Score</th>
            <th className={`${TH} text-right`}>Base risk</th>
            <th className={`${TH} text-left`}>Filters</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={10} className="px-3 py-7 text-center text-on-surface-variant">
                {loading ? 'Loading…' : 'No continuation triggers on this day.'}
              </td>
            </tr>
          )}
          {rows.map((row) => (
            <tr key={row.setup_id} className="border-b border-[#eef0f4]">
              <td className={`${CELL} font-data`}>{timeIst(row.trigger_time)}</td>
              <td className={CELL}>
                <span className="inline-flex items-center gap-2">
                  <Side direction={row.direction} />
                  <span className="font-bold">{row.symbol}</span>
                </span>
              </td>
              <td className={NUM}>{times(row.volume_ratio)}</td>
              <td className={NUM}>{row.stop_pct === null ? '—' : `${row.stop_pct.toFixed(2)}%`}</td>
              <td className={CELL}>
                {row.is_first_trigger_today === null ? '—' : row.is_first_trigger_today ? 'Yes' : 'No'}
              </td>
              <td className={NUM}>{pct(row.align_market_pct)}</td>
              <td className={NUM}>{pct(row.align_sector_pct)}</td>
              <td className={CELL}>
                <ScoreCell row={row} />
              </td>
              <td className={NUM}>{row.base_risk === null ? '—' : inr(row.base_risk, 0)}</td>
              <td className={CELL}>
                {row.passes ? (
                  <span className="label-caps text-positive">Pass</span>
                ) : (
                  <span className="text-[11px] text-on-surface-variant">{label(FUNNEL_REASONS, row.reason)}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {pending > 0 && (
        <div className="px-3 py-2 text-[11px] text-on-surface-variant border-t border-outline-variant">
          {pending} more {pending === 1 ? 'trigger is' : 'triggers are'} being scored.
        </div>
      )}
    </section>
  )
}

export function StrategyPage({ sessionDate }: { sessionDate: string }) {
  const [date, setDate] = useState(sessionDate)
  // Follow the shell's session date when it rolls over (midnight IST).
  useEffect(() => setDate(sessionDate), [sessionDate])
  const { orb, funnel, loading, error, refresh } = useStrategy(date, date === todayIstDate())

  const headline = detectorHeadline(orb)
  const arms = orb?.arms ?? []
  const count = (state: string) => arms.filter((a) => a.state === state).length
  const inPlay = arms.length + (orb?.in_play_not_armed.length ?? 0)
  const unusable = Object.entries(orb?.unusable ?? {}).sort((a, b) => b[1] - a[1])
  const unusableTotal = unusable.reduce((sum, [, n]) => sum + n, 0)
  const baselines = orb?.baselines
  const triggers = funnel?.triggers ?? []
  const passing = triggers.filter((t) => t.passes).length

  return (
    <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar">
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-4 flex flex-col gap-3">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-[16px] font-extrabold text-on-surface">Strategy signals</h1>
            <p className="text-[12px] text-on-surface-variant">
              What ARB’s two signal sources see today. Signals only: the engine does not trade ORB yet.
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

        <h2 className="label-caps text-on-surface-variant mt-1">Opening range breakout</h2>
        <section className="grid grid-cols-2 lg:grid-cols-4 gap-px bg-outline-variant border border-outline-variant rounded-md overflow-hidden">
          <Tile label="Detector" value={headline.value} note={headline.note} tone={headline.tone} />
          <Tile
            label="Baselines"
            value={baselines && baselines.total > 0 ? `${baselines.ready} / ${baselines.total}` : '—'}
            note={
              baselines?.as_of
                ? `ATR14 and opening volume, history to ${baselines.as_of}`
                : 'ATR14 and average opening volume'
            }
            tone={baselines && baselines.total > 0 && baselines.ready < baselines.total ? 'text-amber-700' : 'text-on-surface'}
          />
          <Tile
            label="In play at 09:20"
            value={orb?.ranked ? String(inPlay) : '—'}
            note={`Opening volume ≥ ${orb?.rules.min_rvol ?? 3}× · top ${orb?.rules.top_n ?? 3} armed`}
          />
          <Tile
            label="Outcomes"
            value={arms.length ? `${count('TRIGGERED')} triggered` : '—'}
            note={arms.length ? `${count('ARMED')} waiting · ${count('MISSED')} missed · ${count('EXPIRED')} expired` : undefined}
          />
        </section>

        <ArmsTable orb={orb} loading={loading} />

        {(orb?.in_play_not_armed.length || orb?.near_misses.length || unusableTotal > 0 || baselines?.not_ready.length) ? (
          <section className="bg-surface border border-outline-variant rounded-md px-4 py-3 flex flex-col gap-2.5">
            <StockChips
              title="In play, not armed"
              stocks={orb?.in_play_not_armed ?? []}
              note="At or above the volume line, but outside the top three"
            />
            <StockChips
              title="Near the line"
              stocks={orb?.near_misses ?? []}
              note="Between 2× and the volume line"
            />
            {unusableTotal > 0 && (
              <div className="text-[12px] text-on-surface-variant">
                <span className="label-caps mr-2">No usable range</span>
                {unusable.map(([reason, n]) => `${n} ${label(RANGE_REASONS, reason)}`).join(' · ')}
              </div>
            )}
            {baselines && baselines.not_ready.length > 0 && (
              <div className="text-[12px] text-on-surface-variant">
                <span className="label-caps mr-2">Baseline not ready</span>
                {baselines.not_ready.slice(0, 8).map((b) => (
                  <span key={b.symbol} className="mr-2" title={b.detail ?? undefined}>
                    <span className="font-bold text-on-surface">{b.symbol}</span>{' '}
                    {(b.reason ?? '').replace(/_/g, ' ')}
                  </span>
                ))}
                {baselines.not_ready.length > 8 && <span>and {baselines.not_ready.length - 8} more</span>}
              </div>
            )}
          </section>
        ) : null}

        <div className="flex flex-wrap items-baseline justify-between gap-2 mt-2">
          <h2 className="label-caps text-on-surface-variant">Funnel: continuation triggers</h2>
          <span className="text-[11px] text-on-surface-variant">
            {triggers.length > 0 ? `${passing} of ${triggers.length} pass the filters · ` : ''}
            Shown before the ARB budget and the two-a-day limit on score 0
          </span>
        </div>
        <FunnelTable rows={triggers} pending={funnel?.pending ?? 0} loading={loading && !funnel} />
      </div>
    </div>
  )
}

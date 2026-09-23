import { useEffect, useMemo, useState } from 'react'
import type { ObservationReadiness, RadarRow, RunnerStatus, SessionCoverage } from '../api/types'
import type { FeedStatusView, RunnerPresence } from '../lib/feedStatus'
import { formatPercent, formatPrice, formatTimeIst, formatVolume } from '../lib/format'
import { useSymbolTimeline } from '../hooks/useSymbolTimeline'

type ViewMode = 'sector' | 'grid' | 'feed'

const SECTOR_GROUPS: ReadonlyArray<readonly [string, readonly string[]]> = [
  ['Automobile and Auto Components', ['MARUTI', 'M&M', 'BAJAJ-AUTO', 'EICHERMOT', 'TVSMOTOR', 'HYUNDAI', 'MOTHERSON', 'BOSCHLTD', 'TMPV']],
  ['Capital Goods', ['HAL', 'BEL', 'TMCV', 'ABB', 'SIEMENS', 'CGPOWER', 'CUMMINSIND', 'ENRIN', 'MAZDOCK']],
  ['Chemicals', ['SOLARINDS', 'PIDILITIND']],
  ['Construction', ['LT']],
  ['Construction Materials', ['ULTRACEMCO', 'GRASIM', 'AMBUJACEM', 'SHREECEM']],
  ['Consumer Durables', ['TITAN', 'ASIANPAINT']],
  ['Consumer Services', ['TRENT', 'DMART', 'ETERNAL', 'INDHOTEL']],
  ['Fast Moving Consumer Goods', ['HINDUNILVR', 'ITC', 'NESTLEIND', 'VBL', 'BRITANNIA', 'UNITDSPR', 'TATACONSUM', 'GODREJCP']],
  ['Financial Services', ['HDFCBANK', 'ICICIBANK', 'SBIN', 'BAJFINANCE', 'KOTAKBANK', 'AXISBANK', 'BAJAJFINSV', 'SHRIRAMFIN', 'SBILIFE', 'TATACAP', 'JIOFIN', 'CHOLAFIN', 'UNIONBANK', 'PNB', 'BAJAJHLDNG', 'BANKBARODA', 'HDFCLIFE', 'PFC', 'MUTHOOTFIN', 'CANBK', 'IRFC', 'HDFCAMC', 'RECLTD']],
  ['Healthcare', ['SUNPHARMA', 'DIVISLAB', 'TORNTPHARM', 'APOLLOHOSP', 'CIPLA', 'ZYDUSLIFE', 'DRREDDY', 'MAXHEALTH']],
  ['Information Technology', ['TCS', 'INFY', 'HCLTECH', 'WIPRO', 'TECHM', 'LTM']],
  ['Metals and Mining', ['ADANIENT', 'JSWSTEEL', 'HINDZINC', 'TATASTEEL', 'HINDALCO', 'JINDALSTEL', 'VEDL']],
  ['Oil, Gas and Consumable Fuels', ['RELIANCE', 'COALINDIA', 'ONGC', 'IOC', 'BPCL', 'GAIL']],
  ['Power', ['ADANIPOWER', 'NTPC', 'POWERGRID', 'ADANIGREEN', 'ADANIENSOL', 'TATAPOWER']],
  ['Realty', ['DLF', 'LODHA']],
  ['Services', ['ADANIPORTS', 'INDIGO']],
  ['Telecommunication', ['BHARTIARTL']],
]

interface Props {
  rows: RadarRow[]
  coverage: SessionCoverage | null
  status: RunnerStatus | null
  runnerPresence: RunnerPresence
  feedStatus: FeedStatusView
  observationReadiness: ObservationReadiness | null
  startingObservation: boolean
  observationError: string | null
  sessionDate: string
  externalSearch: string
  loading: boolean
  error: string | null
  onStartObservation: () => void
  onRefresh: () => void
  onExport: () => void
}

function isAttention(row: RadarRow): boolean {
  return /SPIKE|PULLBACK_READY|CONTINUATION_ARMED|TRIGGERED|REJECTED/.test(row.phase)
}

function directionClass(value?: number | null): string {
  if (value == null) return 'text-[#45464d]'
  return value > 0 ? 'text-[#00714e]' : value < 0 ? 'text-[#ba1a1a]' : 'text-[#45464d]'
}

function phaseLabel(row: RadarRow): string {
  if (row.phase === 'IDLE') return 'NORMAL'
  return row.phase.replaceAll('_', ' ')
}

function StockCard({ row, selected, onSelect }: { row: RadarRow; selected: boolean; onSelect: () => void }) {
  const attention = isAttention(row)
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`min-w-0 rounded-[3px] border p-2 text-left transition-colors ${
        selected
          ? 'border-[#005db7] bg-[#eff4ff] ring-1 ring-[#005db7]/20'
          : attention
            ? 'border-[#ffdad6] bg-[#fff8f7] hover:bg-[#ffdad6]/40'
            : 'border-[#e5eeff] bg-white hover:bg-[#eff4ff]'
      }`}
    >
      <span className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-[11px] font-bold text-[#0b1c30]">{row.symbol}</span>
        {attention && <span className="shrink-0 text-[9px] font-mono font-bold text-[#ba1a1a]">● SIGNAL</span>}
      </span>
      <span className="mt-1 flex items-center justify-between gap-2 font-mono text-[11px]">
        <span className="text-[#0b1c30]">₹{formatPrice(row.last_1m_close)}</span>
        <span className={`font-semibold ${directionClass(row.pct_change)}`}>{formatPercent(row.pct_change)}</span>
      </span>
      <span className="mt-1 block truncate font-mono text-[9px] uppercase text-[#76777d]">{phaseLabel(row)}</span>
    </button>
  )
}

function Inspector({ row, sessionDate }: { row: RadarRow | null; sessionDate: string }) {
  const { data, loading, error } = useSymbolTimeline(sessionDate, row?.symbol ?? null)
  if (!row) {
    return <section className="rounded-[4px] border border-[#e5e7eb] bg-white p-4 text-sm text-[#76777d]">Select an instrument to inspect its live radar data.</section>
  }
  const events = data?.setups.flatMap((setup) => setup.events).slice(-5).reverse() ?? []
  return (
    <section className="rounded-[4px] border border-[#e5e7eb] bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-[#e5eeff] px-3 py-2">
        <span className="font-mono text-[10px] font-bold uppercase tracking-[0.5px] text-[#45464d]">Radar Inspector</span>
        <span className={`rounded-[2px] px-1.5 py-0.5 font-mono text-[9px] font-bold ${isAttention(row) ? 'bg-[#ffdad6] text-[#93000a]' : 'bg-[#82f5c1] text-[#00714e]'}`}>
          LIVE TELEMETRY
        </span>
      </div>
      <div className="p-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="font-mono text-[20px] font-bold tracking-tight text-[#0b1c30]">{row.symbol}</h2>
            <p className="mt-1 font-mono text-[10px] text-[#76777d]">NSE · 1-MINUTE OBSERVATION</p>
          </div>
          <span className={`rounded-[2px] px-2 py-1 font-mono text-[10px] font-bold ${directionClass(row.pct_change)}`}>
            {row.direction ?? 'NEUTRAL'}
          </span>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2 border-y border-[#e5eeff] py-3">
          <div><p className="font-mono text-[9px] uppercase text-[#76777d]">Last close</p><p className="mt-1 font-mono text-[16px] font-bold text-[#0b1c30]">₹{formatPrice(row.last_1m_close)}</p></div>
          <div><p className="font-mono text-[9px] uppercase text-[#76777d]">Session move</p><p className={`mt-1 font-mono text-[16px] font-bold ${directionClass(row.pct_change)}`}>{formatPercent(row.pct_change)}</p></div>
          <div><p className="font-mono text-[9px] uppercase text-[#76777d]">Volume</p><p className="mt-1 font-mono text-[12px] font-bold text-[#0b1c30]">{formatVolume(row.volume)}</p></div>
          <div><p className="font-mono text-[9px] uppercase text-[#76777d]">Trigger price</p><p className="mt-1 font-mono text-[12px] font-bold text-[#0b1c30]">{row.trigger_price == null ? '—' : `₹${formatPrice(row.trigger_price)}`}</p></div>
        </div>
        <div className="mt-3 rounded-[2px] bg-[#eff4ff] px-2.5 py-2">
          <p className="font-mono text-[9px] uppercase text-[#76777d]">Current radar state</p>
          <p className="mt-1 text-[12px] font-bold text-[#0b1c30]">{phaseLabel(row)}</p>
          <p className="mt-1 text-[11px] leading-4 text-[#45464d]">{row.last_event || 'Waiting for the next observation event.'}</p>
        </div>
      </div>
      <div className="border-t border-[#e5eeff] px-3 py-2">
        <p className="font-mono text-[10px] font-bold uppercase tracking-[0.5px] text-[#45464d]">Detection feed</p>
        {loading && <p className="mt-2 text-[11px] text-[#76777d]">Loading symbol timeline…</p>}
        {error && <p className="mt-2 text-[11px] text-[#ba1a1a]">{error}</p>}
        {!loading && !error && events.length === 0 && <p className="mt-2 text-[11px] text-[#76777d]">No confirmed setup events for this session.</p>}
        <div className="mt-2 space-y-2">
          {events.map((event) => (
            <div key={`${event.sequence_number}-${event.created_at}`} className="flex gap-2 border-l-2 border-[#e5eeff] pl-2">
              <span className="mt-1 size-1.5 shrink-0 rounded-full bg-[#005db7]" />
              <div className="min-w-0"><p className="text-[11px] font-semibold text-[#0b1c30]">{event.label}</p><p className="font-mono text-[9px] text-[#76777d]">{formatTimeIst(event.created_at)} IST</p></div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

export function RadarStreamWorkstation({
  rows, coverage, status, runnerPresence, feedStatus, observationReadiness, startingObservation, observationError,
  sessionDate, externalSearch, loading, error, onStartObservation, onRefresh, onExport,
}: Props) {
  const [viewMode, setViewMode] = useState<ViewMode>('sector')
  const [localQuery, setLocalQuery] = useState('')
  const [attentionOnly, setAttentionOnly] = useState(false)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [alertAcknowledged, setAlertAcknowledged] = useState(false)
  const query = `${externalSearch} ${localQuery}`.trim().toUpperCase()
  const observedRows = useMemo(() => rows.filter((row) => row.symbol.includes(query)), [rows, query])
  const attentionRows = useMemo(() => observedRows.filter(isAttention), [observedRows])
  const visibleRows = attentionOnly ? attentionRows : observedRows
  const selected = rows.find((row) => row.symbol === selectedSymbol) ?? attentionRows[0] ?? rows[0] ?? null
  const subscribed = coverage?.subscribed ?? status?.subscribed_tokens ?? 100
  const covered = coverage?.tokens_with_1m ?? rows.length
  const normalCount = Math.max(0, covered - attentionRows.length)
  const runnerRunning = runnerPresence === 'running'
  const canStart = observationReadiness?.can_start ?? false

  useEffect(() => {
    if (!selectedSymbol && selected) setSelectedSymbol(selected.symbol)
  }, [selected, selectedSymbol])

  const groupRows = (symbols: readonly string[]) => visibleRows.filter((row) => symbols.includes(row.symbol))
  const ungrouped = visibleRows.filter((row) => !SECTOR_GROUPS.some(([, symbols]) => symbols.includes(row.symbol)))

  return (
    <div className="flex-1 overflow-y-auto bg-[#f8f9ff]">
      <div className="mx-auto max-w-[1720px]">
        <div className="border-b border-[#e5e7eb] bg-white px-4 py-2 font-mono text-[10px] uppercase tracking-[0.6px] text-[#76777d] md:px-6">Desk / Nifty 100 / Radar Stream Console</div>
        <section className="border-b border-[#e5e7eb] bg-white px-4 py-4 md:px-6">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div><div className="flex flex-wrap items-center gap-2"><h1 className="text-[20px] font-bold tracking-tight text-[#0b1c30]">NIFTY 100 RADAR STREAM</h1><span className={`inline-flex items-center gap-1.5 rounded px-2 py-1 font-mono text-[10px] font-bold ${runnerRunning ? 'bg-[#82f5c1] text-[#00714e]' : 'bg-[#e5eeff] text-[#45464d]'}`}><span className={`size-1.5 rounded-full ${runnerRunning ? 'bg-[#006c4a] pulse-green' : 'bg-[#76777d]'}`} />{runnerRunning ? 'OBSERVATION ACTIVE' : 'OBSERVATION STANDBY'}</span></div><p className="mt-1 text-[12px] text-[#45464d]">Real-time multi-sector quantitative anomaly and spike detection for the NIFTY 100.</p></div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex rounded-[3px] bg-[#e5eeff] p-0.5 font-mono text-[10px] uppercase">
                {([['sector', 'Sector-wise grouped'], ['grid', 'All stocks master grid'], ['feed', 'Spike radar feed']] as const).map(([mode, label]) => <button key={mode} type="button" onClick={() => setViewMode(mode)} className={`rounded-[2px] px-2.5 py-1.5 ${viewMode === mode ? 'bg-white font-bold text-[#0b1c30] shadow-sm' : 'text-[#45464d]'}`}>{label}</button>)}
              </div>
              <button type="button" onClick={onRefresh} className="inline-flex items-center gap-1 rounded-[3px] bg-[#eff4ff] px-3 py-2 font-mono text-[10px] font-bold uppercase text-[#0b1c30] hover:bg-[#e5eeff]"><span className="material-symbols-outlined text-[15px]">radar</span>Scan anomalies</button>
              {runnerRunning ? <span className="rounded-[3px] bg-[#0b1c30] px-3 py-2 font-mono text-[10px] font-bold uppercase text-white">Observation running</span> : <button type="button" onClick={onStartObservation} disabled={!canStart || startingObservation || runnerPresence === 'unknown'} title={!canStart ? observationReadiness?.reason : 'Start the existing gated observation runner'} className="inline-flex items-center gap-1 rounded-[3px] bg-[#0b1c30] px-3 py-2 font-mono text-[10px] font-bold uppercase text-white disabled:cursor-not-allowed disabled:opacity-50"><span className="material-symbols-outlined text-[15px]">sensors</span>{startingObservation ? 'Starting…' : 'Start observation'}</button>}
            </div>
          </div>
          <div className="mt-4 flex flex-col gap-2 border-t border-[#e5eeff] pt-3 font-mono text-[10px] sm:flex-row sm:items-center"><span className="font-bold uppercase text-[#0b1c30]">Constituent health:</span><span className="font-bold text-[#00714e]">{covered} of {subscribed} covered</span><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[#e5eeff]"><div className="h-full bg-[#006c4a]" style={{ width: `${Math.min(100, Math.round((covered / Math.max(1, subscribed)) * 100))}%` }} /></div><span className="text-[#00714e]">● {normalCount} normal</span><span className="text-[#ba1a1a]">● {attentionRows.length} signals</span><span className="text-[#45464d]">Feed: {feedStatus.code}</span></div>
        </section>
        <div className="space-y-4 px-4 py-4 md:px-6">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="Current trading day" value={sessionDate} detail={runnerRunning ? 'Active observation session' : 'Selected session'} tone="neutral" />
            <Metric label="Observation status" value={runnerRunning ? 'RUNNING' : 'STANDBY'} detail={feedStatus.detail} tone={runnerRunning ? 'positive' : 'neutral'} />
            <Metric label="Coverage and pass rate" value={`${covered} / ${subscribed}`} detail={`${normalCount} normal · ${attentionRows.length} signal watch`} tone="positive" />
            <Metric label="Radar signals detected" value={`${attentionRows.length} FLAGGED`} detail={attentionRows.length ? 'Review active anomalies below' : 'No active attention signals'} tone={attentionRows.length ? 'danger' : 'neutral'} />
          </div>
          {observationError && <div className="border-l-4 border-[#ba1a1a] bg-[#ffdad6]/50 px-3 py-2 text-[12px] text-[#93000a]">{observationError}</div>}
          {error && <div className="border-l-4 border-[#ba1a1a] bg-[#ffdad6]/50 px-3 py-2 text-[12px] text-[#93000a]">{error}</div>}
          {!alertAcknowledged && attentionRows.length > 0 && <div className="flex flex-col gap-3 rounded-[3px] border-l-4 border-[#ba1a1a] bg-[#ffdad6]/40 p-3 md:flex-row md:items-center md:justify-between"><div className="min-w-0"><p className="font-mono text-[10px] font-bold uppercase tracking-[0.5px] text-[#93000a]">Active radar signals ({attentionRows.length} requiring review)</p><p className="mt-1 truncate font-mono text-[11px] text-[#0b1c30]">{attentionRows.slice(0, 6).map((row) => `${row.symbol} — ${phaseLabel(row)}`).join(' · ')}</p></div><div className="flex shrink-0 gap-2"><button type="button" onClick={() => setSelectedSymbol(attentionRows[0]?.symbol ?? null)} className="rounded-[2px] bg-[#ba1a1a] px-2.5 py-1.5 font-mono text-[10px] font-bold uppercase text-white">Inspect first signal</button><button type="button" onClick={() => setAlertAcknowledged(true)} className="rounded-[2px] bg-[#e5eeff] px-2.5 py-1.5 font-mono text-[10px] uppercase text-[#0b1c30]">Acknowledge</button></div></div>}
          <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-12">
            <section className="space-y-3 xl:col-span-8">
              <div className="flex flex-col gap-2 rounded-[4px] bg-white p-3 shadow-sm md:flex-row md:items-center md:justify-between"><div className="flex flex-1 flex-wrap items-center gap-2"><label className="relative min-w-[220px] flex-1 md:max-w-sm"><span className="material-symbols-outlined absolute left-2 top-2 text-[16px] text-[#76777d]">search</span><input value={localQuery} onChange={(event) => setLocalQuery(event.target.value)} placeholder="Filter ticker, e.g. INFY, RELIANCE…" className="w-full rounded-[2px] bg-[#eff4ff] py-2 pl-8 pr-2 font-mono text-[11px] text-[#0b1c30] outline-none focus:ring-1 focus:ring-[#005db7]" /></label><button type="button" onClick={() => setAttentionOnly(false)} className={`rounded-[2px] px-2 py-1.5 font-mono text-[10px] ${!attentionOnly ? 'bg-[#d3e4fe] font-bold text-[#0b1c30]' : 'bg-[#eff4ff] text-[#45464d]'}`}>All ({observedRows.length})</button><button type="button" onClick={() => setAttentionOnly(true)} className={`rounded-[2px] px-2 py-1.5 font-mono text-[10px] ${attentionOnly ? 'bg-[#ffdad6] font-bold text-[#93000a]' : 'bg-[#eff4ff] text-[#ba1a1a]'}`}>Signals ({attentionRows.length})</button></div><button type="button" onClick={onExport} className="rounded-[2px] border border-[#e5e7eb] px-2.5 py-1.5 font-mono text-[10px] uppercase text-[#45464d]">Export CSV</button></div>
              {loading && visibleRows.length === 0 ? <div className="rounded-[4px] bg-white p-10 text-center text-sm text-[#76777d]">Loading radar data…</div> : viewMode === 'sector' ? <SectorView groups={SECTOR_GROUPS} rowsForGroup={groupRows} ungrouped={ungrouped} selected={selected?.symbol ?? null} onSelect={setSelectedSymbol} /> : viewMode === 'grid' ? <MasterGrid rows={visibleRows} selected={selected?.symbol ?? null} onSelect={setSelectedSymbol} /> : <SignalFeed rows={attentionRows} selected={selected?.symbol ?? null} onSelect={setSelectedSymbol} />}
            </section>
            <aside className="space-y-3 xl:col-span-4"><Inspector row={selected} sessionDate={sessionDate} /><section className="rounded-[4px] border border-[#0b1c30] bg-[#0b1c30] p-3 text-slate-200"><div className="flex items-center justify-between"><p className="font-mono text-[10px] font-bold uppercase tracking-[0.5px]">Live console output</p><span className={`size-1.5 rounded-full ${runnerRunning ? 'bg-[#82f5c1] pulse-green' : 'bg-slate-500'}`} /></div><div className="mt-3 space-y-1 font-mono text-[10px] leading-4 text-slate-300"><p>RADAR&gt; {runnerRunning ? 'Observation runner connected' : 'Observation runner is not active'}</p><p>FEED&gt; {feedStatus.detail}</p><p>ROWS&gt; {rows.length} symbols returned for {sessionDate}</p><p>UPDATED&gt; {status?.updated_at ? `${formatTimeIst(status.updated_at)} IST` : 'Awaiting status'}</p></div></section></aside>
          </div>
        </div>
      </div>
    </div>
  )
}

function Metric({ label, value, detail, tone }: { label: string; value: string; detail: string; tone: 'positive' | 'danger' | 'neutral' }) {
  const color = tone === 'positive' ? 'text-[#00714e]' : tone === 'danger' ? 'text-[#ba1a1a]' : 'text-[#0b1c30]'
  return <section className="rounded-[4px] border-b border-[#e5e7eb] bg-white p-3 shadow-sm"><p className="font-mono text-[10px] font-semibold uppercase tracking-[0.5px] text-[#76777d]">{label}</p><p className={`mt-3 text-[18px] font-bold ${color}`}>{value}</p><p className="mt-1 font-mono text-[10px] text-[#45464d]">{detail}</p><div className={`mt-3 h-1 rounded-full ${tone === 'positive' ? 'bg-[#006c4a]' : tone === 'danger' ? 'bg-[#ba1a1a]' : 'bg-[#d3e4fe]'}`} /></section>
}

function SectorView({ groups, rowsForGroup, ungrouped, selected, onSelect }: { groups: typeof SECTOR_GROUPS; rowsForGroup: (symbols: readonly string[]) => RadarRow[]; ungrouped: RadarRow[]; selected: string | null; onSelect: (symbol: string) => void }) {
  return <div className="space-y-2">{groups.map(([name, symbols]) => { const rows = rowsForGroup(symbols); if (!rows.length) return null; const signals = rows.filter(isAttention).length; return <section key={name} className="overflow-hidden rounded-[4px] bg-white shadow-sm"><div className="flex items-center justify-between gap-3 bg-[#eff4ff] px-3 py-2"><div className="min-w-0"><h2 className="truncate text-[13px] font-semibold text-[#0b1c30]">{name}</h2><p className="font-mono text-[9px] uppercase text-[#76777d]">{rows.length} observed symbols</p></div>{signals > 0 && <span className="shrink-0 rounded-[2px] bg-[#ffdad6] px-1.5 py-1 font-mono text-[9px] font-bold text-[#93000a]">{signals} SIGNAL{signals === 1 ? '' : 'S'}</span>}</div><div className="grid grid-cols-1 gap-1.5 p-2 sm:grid-cols-2 xl:grid-cols-3">{rows.map((row) => <StockCard key={row.symbol} row={row} selected={selected === row.symbol} onSelect={() => onSelect(row.symbol)} />)}</div></section> })}{ungrouped.length > 0 && <section className="rounded-[4px] bg-white p-2 shadow-sm"><p className="px-1 pb-2 font-mono text-[10px] font-bold uppercase text-[#76777d]">Other observed symbols</p><div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 xl:grid-cols-3">{ungrouped.map((row) => <StockCard key={row.symbol} row={row} selected={selected === row.symbol} onSelect={() => onSelect(row.symbol)} />)}</div></section>}</div>
}

function MasterGrid({ rows, selected, onSelect }: { rows: RadarRow[]; selected: string | null; onSelect: (symbol: string) => void }) {
  return <div className="overflow-x-auto rounded-[4px] bg-white shadow-sm"><table className="w-full min-w-[740px] border-collapse text-left"><thead className="bg-[#eff4ff]"><tr>{['Symbol', 'Last close', 'Move', 'Phase', 'Spike', 'Volume', 'Last event', 'Updated'].map((label) => <th key={label} className="border-b border-[#e5e7eb] px-3 py-2 font-mono text-[10px] font-bold uppercase text-[#45464d]">{label}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={row.symbol} onClick={() => onSelect(row.symbol)} className={`cursor-pointer border-b border-[#e5eeff] text-[11px] hover:bg-[#eff4ff] ${selected === row.symbol ? 'bg-[#eff4ff]' : ''}`}><td className="px-3 py-2 font-mono font-bold text-[#0b1c30]">{row.symbol}</td><td className="px-3 py-2 font-mono">₹{formatPrice(row.last_1m_close)}</td><td className={`px-3 py-2 font-mono font-semibold ${directionClass(row.pct_change)}`}>{formatPercent(row.pct_change)}</td><td className="px-3 py-2"><span className={`rounded-[2px] px-1.5 py-1 font-mono text-[9px] ${isAttention(row) ? 'bg-[#ffdad6] text-[#93000a]' : 'bg-[#e5eeff] text-[#45464d]'}`}>{phaseLabel(row)}</span></td><td className="px-3 py-2 text-[#45464d]">{row.spike}</td><td className="px-3 py-2 font-mono">{formatVolume(row.volume)}</td><td className="max-w-[180px] truncate px-3 py-2 text-[#45464d]">{row.last_event}</td><td className="px-3 py-2 font-mono text-[#76777d]">{formatTimeIst(row.updated_at)}</td></tr>)}</tbody></table></div>
}

function SignalFeed({ rows, selected, onSelect }: { rows: RadarRow[]; selected: string | null; onSelect: (symbol: string) => void }) {
  if (!rows.length) return <div className="rounded-[4px] bg-white p-10 text-center text-sm text-[#76777d]">No active radar signals for this filter.</div>
  return <div className="space-y-2">{rows.map((row) => <button key={row.symbol} type="button" onClick={() => onSelect(row.symbol)} className={`w-full rounded-[4px] border-l-4 border-[#ba1a1a] bg-white p-3 text-left shadow-sm hover:bg-[#fff8f7] ${selected === row.symbol ? 'ring-1 ring-[#ba1a1a]/30' : ''}`}><div className="flex items-center justify-between gap-3"><div><p className="font-mono text-[13px] font-bold text-[#0b1c30]">{row.symbol} <span className="text-[10px] text-[#ba1a1a]">{phaseLabel(row)}</span></p><p className="mt-1 text-[11px] text-[#45464d]">{row.last_event}</p></div><div className="text-right"><p className="font-mono text-[12px] font-bold text-[#0b1c30]">₹{formatPrice(row.last_1m_close)}</p><p className={`font-mono text-[10px] font-bold ${directionClass(row.pct_change)}`}>{formatPercent(row.pct_change)}</p></div></div></button>)}</div>
}

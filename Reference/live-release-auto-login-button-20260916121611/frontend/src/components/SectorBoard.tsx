import { useEffect, useState } from 'react'
import { fetchSectorMap, type SectorMap } from '../api/client'
import type { RadarRow } from '../api/types'
import { formatPrice, formatPercent } from '../lib/format'
import { SymbolTimelinePanel } from './SymbolTimelinePanel'
import { useSymbolTimeline } from '../hooks/useSymbolTimeline'

function Detail({ symbol, sessionDate }: {symbol: string; sessionDate: string}) {
  const { data, loading, error, refresh } = useSymbolTimeline(sessionDate, symbol, 0)
  return <><button className="m-3 px-3 py-2 border border-outline-variant text-sm" onClick={() => void refresh()}>Refresh details</button><SymbolTimelinePanel symbol={symbol} data={data} loading={loading} error={error} onRetry={() => void refresh()} /></>
}

export function SectorBoard({ rows, loading, sessionDate, search }: {
  rows: RadarRow[]; loading: boolean; sessionDate: string; search: string
}) {
  const [map, setMap] = useState<SectorMap | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attention, setAttention] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  useEffect(() => { let active = true; fetchSectorMap().then(value => {if(active) setMap(value)})
    .catch(e => {if(active) setError(String(e))}); return () => {active = false} }, [])
  useEffect(() => {setSelected(null)}, [sessionDate])
  const bySymbol = new Map(rows.map(row => [row.symbol, row]))
  const needsAttention = (row?: RadarRow) => Boolean(row && /trigger|ready|reject|fail|invalid/i.test(`${row.phase} ${row.last_event} ${row.continuation}`))
  if (error || (map && !map.valid)) return <div role="alert" className="p-4 text-negative">{error || map?.reason}</div>
  if (!map) return <p className="p-4">Loading sector map…</p>
  return <div className="sector-workspace">
    <div className="sector-tools"><span>{map.symbol_count} names · {map.sectors.length} sectors</span>
      <label><input type="checkbox" checked={attention} onChange={e => setAttention(e.target.checked)} /> Attention only ({rows.filter(needsAttention).length})</label>
      <label>Jump to <select defaultValue="" onChange={e => document.getElementById(e.target.value)?.scrollIntoView({block:'start'})}><option value="">Sector</option>{map.sectors.map((g,i) => <option value={`sector-${i}`} key={g.name}>{g.name}</option>)}</select></label>
      <span className="text-on-surface-variant">{loading ? 'Refreshing…' : `${rows.length} observed`} · {map.version}</span>
    </div>
    <div className="sector-scroll">{map.sectors.map((group,index) => {
      const symbols = group.symbols.filter(symbol => symbol.includes(search.trim().toUpperCase()) && (!attention || needsAttention(bySymbol.get(symbol))))
      if (!symbols.length) return null
      return <section id={`sector-${index}`} key={group.name} className="sector-section"><h2>{group.name}<span>{symbols.length}</span></h2>
        <div className="sector-grid">{symbols.map(symbol => {const row=bySymbol.get(symbol); return <button key={symbol} className={`stock-cell ${needsAttention(row) ? 'stock-attention' : ''}`} onClick={() => setSelected(symbol)}>
          <span className="stock-name">{symbol}<span className={row?.pct_change != null && row.pct_change < 0 ? 'text-negative' : 'text-on-surface-variant'}>{formatPercent(row?.pct_change)}</span></span>
          <span className="stock-price">{formatPrice(row?.last_1m_close)}<span>{row?.direction ?? '—'}</span></span>
          <span className="stock-phase">{row?.phase ?? 'Waiting for data'}</span>
        </button>})}</div></section>
    })}{attention && !rows.some(needsAttention) && <p className="p-4">No attention flags in the current observation snapshot.</p>}</div>
    {selected && <aside className="sector-detail" aria-label={`${selected} setup details`}><div className="sector-tools"><h2>{selected}</h2><button onClick={() => setSelected(null)}>Close details</button></div><Detail symbol={selected} sessionDate={sessionDate}/></aside>}
  </div>
}

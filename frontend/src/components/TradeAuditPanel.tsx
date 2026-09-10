import {useEffect, useState} from 'react'
import {fetchTradeAudit, type TradeAudit} from '../api/client'

const money = (n: number | null | undefined) => n == null || !Number.isFinite(n) ? '—' : `₹${n.toLocaleString('en-IN', {maximumFractionDigits:2})}`
const label = (s: string) => s.replaceAll('_',' ')
function SavedFields({data}: {data: Record<string,unknown>}) {
  return <dl className="audit-fields">{Object.entries(data).map(([key,value]) => <div key={key}><dt>{label(key)}</dt><dd>{value != null && typeof value === 'object' ? <SavedFields data={value as Record<string,unknown>}/> : value == null ? 'Not recorded' : String(value)}</dd></div>)}</dl>
}

export function TradeAuditPanel({rows}: {rows: {trade_id: string; symbol: string}[]}) {
  const [id,setId] = useState('')
  const [audit,setAudit] = useState<TradeAudit | null>(null)
  const [error,setError] = useState('')
  const [now,setNow] = useState(Date.now())
  const [received,setReceived] = useState(0)
  useEffect(() => {
    if(!id) return
    let stopped=false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {const data=await fetchTradeAudit(id); if(!stopped) {setAudit(data); setError(''); setReceived(Date.now())}}
      catch(e) {if(!stopped) setError(e instanceof Error ? e.message : 'Audit unavailable')}
      if(!stopped) timer=setTimeout(poll,2000)
    }
    void poll()
    const clock=setInterval(() => setNow(Date.now()),500)
    return () => {stopped=true; clearTimeout(timer); clearInterval(clock)}
  },[id])
  const t = audit?.trade
  const incomplete = t?.pnl_provisional || (t?.entry_value_est ?? 0)>0 || (t?.exit_value_est ?? 0)>0
  const stale = !received || now-received>5000
  function atStop(stop: number | null | undefined) {
    if(!t || incomplete || t.entry_fill == null || stop == null) return null
    return t.remaining_position_qty * (t.direction === 'UP' ? stop-t.entry_fill : t.entry_fill-stop)
  }
  return <section className="desk-panel"><h2>Trade audit · original setup & execution trail</h2>
    <label className="audit-select">Trade<select value={id} onChange={e => {setId(e.target.value); setAudit(null); setError(''); setReceived(0)}}><option value="">Select a trade</option>{rows.map(r => <option key={r.trade_id} value={r.trade_id}>{r.symbol} · {r.trade_id.slice(0,10)}</option>)}</select></label>
    {error && <p role="alert">{error}</p>}
    {id && !audit && !error && <p>Loading saved audit…</p>}
    {t && <><p>Accounting updated {t.updated_at} · {stale ? 'VIEW STALE' : 'View refreshed'} · {incomplete ? 'PROVISIONAL — unresolved execution prices' : 'Recorded execution prices complete'}</p>
      <div className="audit-quantities">{Object.entries({Intended:t.intended_qty,Filled:t.filled_qty,Exited:t.exited_qty,'Pending entry':t.remaining_entry_qty,Remaining:t.remaining_position_qty,'Confirmed cover':t.protected_qty}).map(([key,value]) => <div key={key}>{key}<strong>{value}</strong></div>)}</div>
      <p>Recorded realised P&L: {money(t.realised_pnl)} {incomplete && '(preserved value; not final)'}</p>
      <p>Live open P&L: {stale || audit?.live_mark.stale || (audit?.live_mark.age_seconds ?? 999)+(now-received)/1000>2 ? 'STALE / unavailable' : money(audit?.live_mark.open_pnl)} · Liquidation mark {money(audit?.live_mark.price)} · Quote as of {audit?.live_mark.quote_as_of ?? 'Unknown'}</p>
      <p>Remaining-position estimate at original stop: {money(atStop(t.initial_stop))} · At current recorded stop: {money(atStop(t.current_stop))}</p>
      <p>Stop estimates exclude charges, slippage and realised exits. They are not guaranteed exit prices or live P&L. Confirmed cover is the last reconciled quantity, not a fresh broker query.</p>
      <details open><summary>Original machine setup · read only</summary>{audit?.original_setup ? <SavedFields data={audit.original_setup}/> : <p>No original snapshot exists for this historical record. It has not been reconstructed.</p>}</details>
      <details><summary>Linked broker execution records</summary>{audit?.orders.map((order,i) => <SavedFields key={String(order.order_id ?? i)} data={order}/>)}</details>
      <h3>Event trail · requests are not confirmations</h3><div className="desk-table"><table><thead><tr><th>Time</th><th>Actor</th><th>Event</th><th>Old stop</th><th>New/requested stop</th><th>Details</th></tr></thead><tbody>{audit?.events.slice().reverse().map(event => <tr key={event.event_id}><td>{event.at}</td><td>{event.actor}</td><td>{label(event.action)}</td><td>{money(event.old_stop)}</td><td>{money(event.new_stop)}</td><td><details><summary>Inspect</summary><SavedFields data={event.payload}/></details></td></tr>)}</tbody></table></div>
    </>}
  </section>
}

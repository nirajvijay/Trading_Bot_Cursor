import {useCallback, useEffect, useRef, useState} from 'react'
import {ApiError, fetchTradingControl, fetchTradingEngineSnapshot, fetchTradingSetups, postStartTradingEngine, postTradingCommand, postTradingPreview, type SetupChoice, type TradePreview, type TradingControl} from '../api/client'
import type {TradingEngineSnapshot} from '../api/types'
import {AdminStepUpModal} from './admin/AdminStepUpModal'
import {TradeAuditPanel} from './TradeAuditPanel'

const money = (n: number | null | undefined) => n == null || !Number.isFinite(n) ? '—' : `₹${n.toLocaleString('en-IN', {maximumFractionDigits: 2})}`
const words = (s: string) => s.replaceAll('_', ' ')

export function TradingDesk({sessionDate}: {sessionDate: string}) {
  const [control, setControl] = useState<TradingControl | null>(null)
  const [snapshot, setSnapshot] = useState<TradingEngineSnapshot | null>(null)
  const [setups, setSetups] = useState<SetupChoice[]>([])
  const [selection, setSelection] = useState('')
  const [preview, setPreview] = useState<TradePreview | null>(null)
  const [qty, setQty] = useState('')
  const [stop, setStop] = useState('')
  const [mode, setMode] = useState<'MANUAL' | 'AUTOPILOT'>('MANUAL')
  const [execution, setExecution] = useState<'PAPER' | 'LIVE'>('PAPER')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [stepUp, setStepUp] = useState(false)
  const retry = useRef<(() => Promise<void>) | null>(null)
  const [received, setReceived] = useState(0)
  const [now, setNow] = useState(Date.now())
  const [trailDrafts, setTrailDrafts] = useState<Record<string,string>>({})
  const stale = !received || now - received > 5000
  const refresh = useCallback(async () => {
    const [c, s, choices] = await Promise.all([fetchTradingControl(), fetchTradingEngineSnapshot(sessionDate), fetchTradingSetups()])
    setControl(c); setSnapshot(s); setSetups(choices.setups); setReceived(Date.now())
  }, [sessionDate])
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {await refresh()} catch (e) {if (!disposed) setError(e instanceof Error ? e.message : 'Desk unavailable')}
      if (!disposed) timer = setTimeout(poll, 1500)
    }
    void poll()
    const clock = setInterval(() => setNow(Date.now()), 500)
    return () => {disposed = true; clearTimeout(timer); clearInterval(clock)}
  }, [refresh])

  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(null)
    try {await action(); retry.current = null}
    catch (e) {
      if (e instanceof ApiError && e.status === 403 && e.message.toLowerCase().includes('step-up')) {
        retry.current = action; setStepUp(true)
      } else setError(e instanceof Error ? e.message : 'Request failed. Check command history before retrying.')
    } finally {setBusy(false)}
  }
  function command(kind: string, extra: Record<string, unknown> = {}) {
    const body = {kind, client_command_id: crypto.randomUUID(), ...extra}
    void perform(async () => {
      const result = await postTradingCommand(body)
      setNotice(`${words(kind)}: ${words(result.state)} — command ${result.command_id}. Acceptance is not broker confirmation.`)
      await refresh()
    })
  }
  const choice = setups.find(s => `${s.setup_id}|${s.continuation_rule_version}` === selection)
  const previewBody = choice ? {setup_id: choice.setup_id, continuation_rule_version: choice.continuation_rule_version,
    ...(qty ? {qty_override: Number(qty)} : {}), ...(stop ? {stop_tighten: Number(stop)} : {})} : null
  const running = control?.strip.engine_state === 'running'
  const controlsReady = !busy && !stale
  return <main className="trading-desk">
    <header className="desk-heading"><div><h1>Trading Desk</h1><p>Session {sessionDate} · {control?.strip.execution_mode ?? 'Unknown mode'} · {stale ? 'STATUS STALE' : words(control?.strip.entry_permission ?? 'unknown')}</p></div><a href="https://kite.zerodha.com/" target="_blank" rel="noreferrer">Open Kite ↗</a></header>
    {error && <div role="alert" className="desk-alert">{error}</div>}
    {notice && <div role="status" className="desk-notice">{notice}</div>}
    <section className="desk-panel"><h2>Session controls</h2><div className="desk-actions">
      <label>Execution<select value={execution} onChange={e => setExecution(e.target.value as 'PAPER'|'LIVE')}><option>PAPER</option><option disabled={!control?.live_execution_authorized}>LIVE</option></select></label>
      <label>Entry decisions<select value={mode} onChange={e => setMode(e.target.value as 'MANUAL'|'AUTOPILOT')}><option>MANUAL</option><option>AUTOPILOT</option></select></label>
      <button disabled={!controlsReady || running || execution === 'LIVE' && !control?.live_execution_authorized} onClick={() => {
        if (execution === 'LIVE' && !window.confirm('Start LIVE engine? This does not arm entries.')) return
        void perform(async () => {await postStartTradingEngine({confirm_live_orders: execution === 'LIVE', session_date: sessionDate}); setNotice('Engine start requested. Entries require a separate arm.'); await refresh()})
      }}>Start engine</button>
      <button disabled={!controlsReady || !running} onClick={() => {
        if (!window.confirm(`Arm ${execution} / ${mode} with saved settings? ${mode === 'AUTOPILOT' ? 'Eligible signals may trade automatically.' : 'Each entry needs your approval.'}`)) return
        command('arm_session', {execution_mode: execution, entry_mode: mode, config_version_id: control?.saved_version_id, live_confirmation: execution === 'LIVE'})
      }}>Arm session</button>
      <button disabled={busy} onClick={() => command('pause_entries')}>Pause entries</button>
      <button disabled={busy} onClick={() => command('disarm')}>Disarm</button>
      <button disabled={!controlsReady || !running} onClick={() => command('switch_manual')}>Switch to manual</button>
      <button disabled={busy} onClick={() => {if(window.confirm('Close all engine-owned positions and pause entries? Unknown orders may require recovery.')) command('close_all')}}>Close all & pause</button>
      <button disabled={busy} onClick={() => command('stop_engine')}>Stop engine · drain</button>
      <button disabled={!controlsReady || !running} onClick={() => command('reconcile_now')}>Reconcile now</button>
    </div><p>Pause blocks new entries. Disarm removes permission. Stop manages existing exposure until flat; it does not immediately close positions. PAPER orders stay simulated.</p></section>
    <div className="desk-metrics"><div>Effective capital<strong>{money(control?.effective.allocated_capital_inr)}</strong></div><div>Daily loss cap<strong>{money(control?.effective.daily_loss_cap_inr)}</strong></div><div>Reserved risk<strong>{stale ? '—' : money(snapshot?.committed_risk)}</strong></div><div>Open P&L<strong>{stale ? 'STALE' : money(control?.strip.open_pnl)}</strong></div></div>
    <section className="desk-panel"><h2>Manual entry review</h2><div className="desk-actions">
      <label>Observed setup<select value={selection} onChange={e => {setSelection(e.target.value); setPreview(null); setQty(''); setStop('')}}><option value="">Select a signal</option>{setups.map(s => <option key={`${s.setup_id}|${s.continuation_rule_version}`} value={`${s.setup_id}|${s.continuation_rule_version}`}>{s.tradingsymbol} · {s.direction} · {s.signal_age_seconds == null ? 'age unknown' : `${Math.floor(s.signal_age_seconds)}s`}</option>)}</select></label>
      <label>Reduce quantity<input type="number" min="1" step="1" placeholder="Automatic sizing" value={qty} onChange={e => {setQty(e.target.value); setPreview(null)}}/></label>
      <label>Tighten initial stop<input type="number" min="0" step="any" placeholder="Structural stop" value={stop} onChange={e => {setStop(e.target.value); setPreview(null)}}/></label>
      <button disabled={!controlsReady || !previewBody} onClick={() => void perform(async () => {if(previewBody) setPreview(await postTradingPreview(previewBody))})}>Preview</button>
    </div>{preview && <div className="desk-preview"><p>{preview.symbol} · {preview.direction} · {preview.proposed_qty} shares · LIMIT {money(preview.limit_price)} · Stop {money(preview.proposed_stop)} · Risk estimate {money(preview.risk_inr)} · Notional {money(preview.notional)}</p><p>{preview.eligible ? 'Preview eligible. All checks run again on approval; prices and eligibility may change.' : `Blocked: ${preview.blockers.map(words).join(', ')}`}</p><button disabled={!controlsReady || !preview.eligible || control?.strip.entry_mode !== 'MANUAL'} onClick={() => {if(previewBody && window.confirm(`Approve ${preview.symbol} entry? The engine will revalidate before sending.`)) {command('approve_entry', previewBody); setPreview(null)}}}>Approve entry</button></div>}</section>
    <section className="desk-panel"><h2>Active positions</h2>{!snapshot ? <p>Position state unavailable.</p> : !snapshot.active.length ? <p>No active trades in this session.</p> : snapshot.active.map(row => <article className="desk-position" key={row.trade_id}><h3>{row.symbol} · {row.direction === 'UP' ? 'BUY' : 'SELL'} · {words(row.status)}</h3><p>Entry {money(row.entry_fill)} · Initial stop {money(row.initial_stop)} · Confirmed stop {money(row.current_stop)}</p><div className="desk-actions"><label>Requested stop<input type="number" step={row.tick_size || 0.05} value={trailDrafts[row.trade_id] ?? ''} placeholder={String(row.current_stop ?? '')} onChange={e => setTrailDrafts({...trailDrafts, [row.trade_id]: e.target.value})}/></label><button disabled={!controlsReady || !Number(trailDrafts[row.trade_id])} onClick={() => command('trail_stop', {trade_id: row.trade_id, new_stop: Number(trailDrafts[row.trade_id])})}>Request trail</button><button disabled={!controlsReady} onClick={() => command('set_auto_trail', {trade_id: row.trade_id, enabled: !row.auto_trail_enabled})}>{row.auto_trail_enabled ? 'Disable' : 'Enable'} auto-trail</button><button disabled={busy} onClick={() => {if(window.confirm(`Close ${row.symbol}?`)) command('close_position', {trade_id: row.trade_id})}}>Close position</button></div></article>)}</section>
    <section className="desk-panel"><h2>Command progress</h2><p>Only “succeeded” confirms completion. Unknown or awaiting-broker commands must be reconciled before retrying.</p><div className="desk-table"><table><thead><tr><th>ID</th><th>Action</th><th>State</th><th>Detail</th></tr></thead><tbody>{control?.commands.slice(0,30).map(c => <tr key={c.command_id}><td>{c.command_id}</td><td>{words(c.kind)}</td><td>{words(c.state)}</td><td>{String(c.result?.error ?? c.result?.reason ?? c.result?.message ?? '')}</td></tr>)}</tbody></table></div></section>
    <section className="desk-panel"><h2>Session history</h2><div className="desk-table"><table><thead><tr><th>Symbol</th><th>Outcome</th><th>Reason</th><th>Recorded P&L</th></tr></thead><tbody>{[...(snapshot?.closed ?? []), ...(snapshot?.skipped ?? [])].map(r => <tr key={r.trade_id}><td>{r.symbol}</td><td>{words(r.status)}</td><td>{words(r.close_reason || r.skip_reason || r.reject_reason || '—')}</td><td>{r.status === 'closed' ? money(r.realised_pnl) : '—'}</td></tr>)}</tbody></table></div></section>
    <section className="desk-panel"><h2>Daily report</h2><p>Trade outcomes and engineering incidents are reported separately, with PAPER, LIVE and unknown-provenance records kept apart. Incomplete prices are excluded from closed-trade totals.</p><a href={`/api/v1/trading-engine/report?session_date=${encodeURIComponent(sessionDate)}&download=true`}>Download session report (JSON)</a></section>
    <TradeAuditPanel rows={[...(snapshot?.active ?? []), ...(snapshot?.closed ?? []), ...(snapshot?.skipped ?? [])]}/>
    <AdminStepUpModal open={stepUp} title="Confirm trading control" onClose={() => setStepUp(false)} onSuccess={() => {const action = retry.current; if(action) void perform(action)}}/>
  </main>
}

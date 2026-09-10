import {useCallback, useEffect, useRef, useState} from 'react'
import {ApiError, fetchAdminConfig, fetchTradingControl, fetchObservationReadiness, patchAdminConfig, postAdminRollback, postTradingCommand, type TradingControl} from '../../api/client'
import type {AdminConfigResponse, AdminConfigValues, ObservationReadiness} from '../../api/types'
import {AdminAuditPanel} from './AdminAuditPanel'
import {AdminStepUpModal} from './AdminStepUpModal'

const tabs = ['Overview','Trading bot values','Strategy · VWAP','Observation','Safety','Audit','Recovery'] as const
type Tab = typeof tabs[number]
const names: Record<string,string> = {
  preferred_execution_mode:'Preferred execution mode (preference only)',
  auto_trail_default_enabled:'Auto-trailing default (1 on / 0 off)',
  trail_stage_one_r:'First trailing stage (+R)', trail_stage_two_r:'Second trailing stage (+R)',
  trail_stage_one_gap_r:'First-stage distance behind extreme (R)', trail_stage_two_gap_r:'Second-stage distance behind extreme (R)',
  trail_modify_interval_seconds:'Minimum trail interval (seconds)', trail_min_improvement_ticks:'Minimum trail improvement (ticks)',
  allocated_capital_inr:'Allocated capital (₹)', per_trade_risk_cap_inr:'Normal trade risk (₹)', limited_per_trade_risk_cap_inr:'LIMITED trade risk (₹)', daily_loss_cap_inr:'Daily loss cap (₹)',
  max_concurrent_positions:'Maximum concurrent positions', max_filled_setups_per_day:'Filled setups per day', one_position_or_unresolved_entry_per_symbol:'One position / pending entry per symbol', aggregate_notional_cap_equals_allocated_capital:'Notional capped by allocated capital',
  round_trip_charge_bps:'Estimated round-trip charges (bps)', estimated_slippage_bps:'Estimated slippage (bps)',
  setup_expiry_seconds:'Signal expiry (seconds)', max_quote_age_seconds:'Maximum quote age (seconds)', max_entry_drift_r:'Maximum entry drift (R)',
  vwap_accept_gap_exclusive_max:'VWAP ACCEPT gap, exclusive (%)', vwap_limited_gap_inclusive_max:'VWAP LIMITED gap, inclusive (%)',
  protection_confirm_deadline_seconds:'Protection deadline (seconds)', entry_remainder_cancel_seconds:'Cancel entry remainder after (seconds)', entry_cutoff_ist:'Entry cutoff (HHMM IST)', square_off_ist:'Square-off (HHMM IST)',
}
const strategy = new Set(['setup_expiry_seconds','max_quote_age_seconds','max_entry_drift_r','vwap_accept_gap_exclusive_max','vwap_limited_gap_inclusive_max'])
const safety = new Set(['protection_confirm_deadline_seconds','entry_remainder_cancel_seconds','entry_cutoff_ist','square_off_ist','one_position_or_unresolved_entry_per_symbol','aggregate_notional_cap_equals_allocated_capital'])
const percent = (key: string) => key.startsWith('vwap_')
const display = (key: string, value: number | string) => key === 'preferred_execution_mode' ? String(value) : String(Number((Number(value) * (percent(key) ? 100 : 1)).toFixed(8)))
const formOf = (c: AdminConfigResponse) => Object.fromEntries(Object.entries(c.values).map(([k,v]) => [k,display(k,v)]))

export function AdminWorkspace() {
  const [tab,setTab] = useState<Tab>('Overview')
  const [config,setConfig] = useState<AdminConfigResponse | null>(null)
  const [control,setControl] = useState<TradingControl | null>(null)
  const [observation,setObservation] = useState<ObservationReadiness | null>(null)
  const [statusReceived,setStatusReceived] = useState(0)
  const [now,setNow] = useState(Date.now())
  const [draft,setDraft] = useState<Record<string,string>>({})
  const [baseVersion,setBaseVersion] = useState('')
  const [comment,setComment] = useState('')
  const [error,setError] = useState('')
  const [notice,setNotice] = useState('')
  const [busy,setBusy] = useState(false)
  const [stepUp,setStepUp] = useState(false)
  const pending = useRef<(() => Promise<void>) | null>(null)
  const draftLoaded = useRef(false)
  const refresh = useCallback(async () => {
    const [c,s,o] = await Promise.all([fetchAdminConfig(),fetchTradingControl(),fetchObservationReadiness().catch(() => null)])
    setConfig(c); setControl(s)
    setObservation(o); setStatusReceived(Date.now())
    if(!draftLoaded.current) {draftLoaded.current=true;setDraft(formOf(c));setBaseVersion(c.version_id)}
  },[])
  useEffect(() => {let stopped=false; let timer: ReturnType<typeof setTimeout>
    async function poll() {try {await refresh()} catch(e) {if(!stopped) setError(e instanceof Error ? e.message : 'Admin unavailable')} if(!stopped) timer=setTimeout(poll,3000)}
    const clock=setInterval(() => setNow(Date.now()),1000)
    void poll(); return () => {stopped=true; clearTimeout(timer);clearInterval(clock)}
  },[refresh])
  async function run(action: () => Promise<void>) {
    setBusy(true); setError('')
    try {await action(); pending.current=null}
    catch(e) {if(e instanceof ApiError && e.status === 403 && e.message.toLowerCase().includes('step-up')) {pending.current=action;setStepUp(true)} else setError(e instanceof Error ? e.message : 'Operation failed')}
    finally {setBusy(false)}
  }
  function command(kind: string) {
    const body={kind,client_command_id:crypto.randomUUID()}
    void run(async () => {const c=await postTradingCommand(body);setNotice(`${kind.replaceAll('_',' ')}: ${c.state}. Check Desk command progress for completion.`);await refresh()})
  }
  function discard() {if(config) {setDraft(formOf(config));setBaseVersion(config.version_id);setComment('');setError('')}}
  function save() {
    const captured={...draft}, version=baseVersion, note=comment
    void run(async () => {
      const values: Record<string,number|string>={}
      for(const [key,value] of Object.entries(captured)) {
        if(key==='preferred_execution_mode') {if(!['PAPER','LIVE'].includes(value)) throw new Error('Choose PAPER or LIVE');values[key]=value;continue}
        if(!value.trim() || !Number.isFinite(Number(value))) throw new Error(`${names[key] ?? key}: enter a finite number`)
        values[key]=Number(value)/(percent(key)?100:1)
      }
      const result=await patchAdminConfig({values:values as AdminConfigValues,expected_version_id:version,comment:note || null})
      setConfig(result);setDraft(formOf(result));setBaseVersion(result.version_id);setComment('');setNotice('Saved. Effective values below show what the engine is using.');await refresh()
    })
  }
  const conflict=!!config && config.version_id!==baseVersion
  const dirty=!!config && Object.entries(draft).some(([k,v]) => v!==display(k,config.values[k]))
  const fieldKeys=Object.keys(draft).filter(k => tab==='Strategy · VWAP' ? strategy.has(k) : tab==='Safety' ? safety.has(k) : tab==='Trading bot values' ? !strategy.has(k)&&!safety.has(k) : false)
  return <main className="trading-desk admin-workspace"><header className="desk-heading"><div><h1>Admin</h1><p>One owner · Saved settings are not automatically armed</p></div></header>
    <div className="desk-panel desk-actions"><button disabled={busy} onClick={() => command('pause_entries')}>Pause entries</button><button disabled={busy} onClick={() => command('disarm')}>Disarm</button><button disabled={busy} onClick={() => {if(window.confirm('Close all engine-owned positions and pause?')) command('close_all')}}>Close all & pause</button><button disabled={busy} onClick={() => command('stop_engine')}>Stop engine · drain</button></div>
    <nav className="admin-tabs" aria-label="Admin sections">{tabs.map(t => <button key={t} aria-current={tab===t?'page':undefined} onClick={() => setTab(t)}>{t}</button>)}</nav>
    <section className="desk-panel" aria-label="Process status"><h2>Process status</h2>
      {!statusReceived || now-statusReceived>5000 ? <p role="status">STATUS STALE — reconnect before relying on process state.</p> : <>
        <p>Trading engine: {control?.strip.engine_state ?? 'unknown'}</p>
        <p>Observation runner: {observation == null ? 'unknown' : observation.runner_running ? 'active / starting' : 'not running'}. Feed: {control?.strip.feed_status ?? 'unknown'}.</p>
        <p>Checklist: {observation?.checklist_status ?? 'unknown'} · {observation?.reason}</p>
        <p>Observation strategy stages share the observation runner; they are not separate processes. A running process does not guarantee fresh market data.</p>
      </>}
    </section>
    {error && <p role="alert" className="desk-alert">{error}</p>}{notice && <p role="status" className="desk-notice">{notice}</p>}
    {conflict && <p role="alert" className="desk-alert">Saved settings changed since this draft began. Discard and reload before editing again; this draft cannot overwrite the newer version.</p>}
    {!config ? <p>Loading configuration…</p> : <>
      {tab==='Overview' && <section className="desk-panel"><h2>Current operating state</h2><p>Engine: {control?.strip.engine_state ?? 'unknown'} · Entry permission: {control?.strip.entry_permission ?? 'unknown'} · {control?.strip.execution_mode ?? 'Unknown mode'}</p><p>Saved version: {config.version_id}</p><p>Effective version: {config.effective_version_id ?? 'Unknown'}</p><p>Waiting for explicit arm: {config.pending_next_arm.length ? config.pending_next_arm.map(k => names[k] ?? k).join(', ') : 'No setting differences'}</p><p>LIVE authorization: {control?.live_execution_authorized ? 'Server enabled — separate LIVE arm required' : 'Disabled'}</p>{config.warnings.map(w => <p key={w} className="desk-alert">{w}</p>)}</section>}
      {!!fieldKeys.length && <section className="desk-panel"><h2>{tab}</h2><div className="admin-fields">{fieldKeys.map(key => <label key={key}>{names[key] ?? key}
        {key === 'preferred_execution_mode'
          ? <select value={draft[key]} onChange={e => setDraft({...draft,[key]:e.target.value})}><option>PAPER</option><option>LIVE</option></select>
          : <input type="number" step={percent(key)?'0.01':'any'} value={draft[key]} onChange={e => setDraft({...draft,[key]:e.target.value})}/>}
        <span>Saved {display(key,config.values[key])} · Effective {config.effective_values[key]==null?'Unknown':display(key,config.effective_values[key])}</span></label>)}</div><p>HHMM uses 1445 for 14:45. Boolean safety settings use 1 for enabled and 0 for disabled. Server validation is authoritative.</p><p>A preferred mode never starts or arms trading. LIVE still requires server authorization and explicit confirmation.</p><p>Risk reductions may take effect immediately. Other changes wait for explicit session arming. Existing trades retain their frozen cost/setup profiles.</p><div className="desk-actions"><label>Change note<input value={comment} onChange={e => setComment(e.target.value)}/></label><button disabled={busy || !dirty || conflict} onClick={save}>Save draft</button><button disabled={busy} onClick={discard}>Discard / reload saved</button></div></section>}
      {tab==='Observation' && <section className="desk-panel"><h2>Observation readiness</h2><p>Feed: {control?.strip.feed_status ?? 'unknown'} · Last sampled tick age: {control?.strip.feed_age_seconds==null?'Unknown':`${control.strip.feed_age_seconds.toFixed(1)} seconds`}</p><p>Use Checklist for Kite login and ordered data preparation. Use Radar to start observation and inspect signals. Observation does not grant permission to trade.</p><p>Regular-session connection opens at 09:00 IST; trading data is expected from 09:15. Special sessions require a complete configured schedule.</p></section>}
      {tab==='Audit' && <AdminAuditPanel currentVersionId={config.version_id} onRollback={async target => {if(!window.confirm('Restore this saved version? Apply policy still governs Effective values.')) return;await run(async () => {await postAdminRollback(target);draftLoaded.current=false;await refresh();setComment('');setNotice('Rollback saved; review Effective values before arming.')})}}/>}
      {tab==='Recovery' && <section className="desk-panel"><h2>Broker reconciliation</h2><p>Unknown ownership is not permission to adopt, cancel or flatten an order. Compare broker positions and orders in Kite before taking action.</p><div className="desk-actions"><button disabled={busy} onClick={() => command('reconcile_now')}>Reconcile now</button><a href="https://kite.zerodha.com/" target="_blank" rel="noreferrer">Open Kite ↗</a></div><h3>Unresolved trade records</h3>{control?.incidents.length ? control.incidents.map((r,i) => <p key={String(r.trade_id ?? i)}>{String(r.symbol)} · {String(r.status)} · {String(r.trade_id)}</p>) : <p>No trade incidents returned. This is not proof of a fresh broker sync.</p>}<h3>Recovery history</h3>{control?.recovery_events.slice(-20).reverse().map((r,i) => <details key={String(r.event_id ?? i)}><summary>{String(r.at)} · {String(r.action).replaceAll('_',' ')}</summary><pre className="audit-raw">{String(r.payload_json ?? '')}</pre></details>)}</section>}
    </>}
    <AdminStepUpModal open={stepUp} title="Confirm Admin change" onClose={() => setStepUp(false)} onSuccess={() => {const action=pending.current;if(action) void run(action)}}/>
  </main>
}

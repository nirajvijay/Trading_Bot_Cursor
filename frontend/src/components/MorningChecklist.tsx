import { useEffect, useState } from 'react'
import { postGenerateLocalData } from '../api/client'
import type { CheckTokenResponse, PreMarketChecklistResponse } from '../api/types'
import { KiteAuthPage } from './KiteAuthPage'

const STEPS = [
  ['instruments', '2', 'Confirm the 100-stock universe', 'instruments'],
  ['historical_candles', '3', 'Prepare prior-session candles', 'historical'],
  ['baselines', '4', 'Calculate volume baselines', 'baselines'],
  ['five_minute_candles', '5', 'Prepare five-minute candles', 'five-minute'],
  ['offline_checks', '6', 'Validate the data pipeline', null],
  ['dashboard_readiness', '7', 'Confirm observation readiness', null],
] as const

export function MorningChecklist({data, loading, error, onRefresh, tokenChecking, onCheckToken}: {
  data: PreMarketChecklistResponse | null; loading: boolean; error: string | null;
  onRefresh: () => void; tokenCheck: CheckTokenResponse | null; tokenCheckedAt: string | null;
  tokenChecking: boolean; onCheckToken: () => Promise<CheckTokenResponse>
}) {
  const [task, setTask] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {if (!task) return; const start=Date.now(); setElapsed(0);
    const timer=setInterval(() => setElapsed(Math.floor((Date.now()-start)/1000)),1000);
    return () => clearInterval(timer)}, [task])
  async function generate(action: string) {
    setTask(action); setMessage(null); setFailure(null)
    try {const result=await postGenerateLocalData(action,data?.session_date); setMessage(result.message); onRefresh()}
    catch(e) {setFailure(e instanceof Error ? e.message : String(e))} finally {setTask(null)}
  }
  return <div className="morning-checklist">
    <header className="page-heading"><div><p className="eyebrow">Morning preparation</p><h1>Checklist</h1><p>{data?.next_step ?? 'Connect Kite, then validate today’s data.'}</p></div>
      <button className="owner-link" disabled={loading || !!task} onClick={onRefresh}>{loading ? 'Checking…' : 'Refresh checks'}</button></header>
    {(error || failure) && <p role="alert" className="notice-error">{error || failure}</p>}
    {message && <p role="status" className="notice-info">{message}</p>}
    {task && <p role="status" className="notice-info">Preparing {task} · {elapsed}s elapsed. Waiting for the completed result; no estimated percentage.</p>}
    <section className="prep-step"><div className="prep-title"><span>1</span><h2>Connect and validate Kite</h2><button disabled={tokenChecking} onClick={() => void onCheckToken().then(onRefresh)}>{tokenChecking ? 'Checking…' : 'Check token'}</button></div><KiteAuthPage /></section>
    {STEPS.map(([key,number,title,action], index) => {
      const area=data?.areas[key]
      const previousReady=!!data && data.areas.kite_auth.status === 'ok' && STEPS.slice(0,index).every(([previous]) => data.areas[previous].status === 'ok')
      return <section key={key} className="prep-step"><div className="prep-title"><span>{number}</span><h2>{title}</h2><strong className={area?.status === 'ok' ? 'text-positive' : 'text-on-surface-variant'}>{area?.status ?? 'Not checked'}</strong></div>
        <p>{area?.message ?? 'Run the checks to see what is needed.'}</p>
        {action && <button disabled={!!task || !previousReady} onClick={() => void generate(action)}>Prepare / update</button>}
        {!previousReady && action && <small>Complete the preceding steps first.</small>}
      </section>
    })}
  </div>
}

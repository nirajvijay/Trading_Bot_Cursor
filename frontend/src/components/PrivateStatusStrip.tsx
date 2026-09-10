import {useEffect, useState} from 'react'
import {fetchTradingControl, type ControlStrip} from '../api/client'

function age(value: number | null | undefined) {return value == null || !Number.isFinite(value) || value < 0 ? 'Unknown' : `${value.toFixed(1)}s`}
export function PrivateStatusStrip() {
  const [strip,setStrip]=useState<ControlStrip | null>(null)
  useEffect(() => {
    let stopped=false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {const data=await fetchTradingControl(); if(!stopped) setStrip(data.strip)}
      catch {if(!stopped) setStrip(null)}
      if(!stopped) timer=setTimeout(poll,2000)
    }
    void poll(); return () => {stopped=true; clearTimeout(timer)}
  },[])
  return <div className="private-status" aria-label="Execution status">
    <strong>{strip?.execution_mode ?? 'Mode unknown'}</strong><span>{strip?.entry_mode ?? 'Unknown'}</span>
    <span>Entries <b>{strip?.entry_permission ?? 'unknown'}</b></span><span>Engine {strip?.engine_state ?? 'unknown'}</span>
    <span>Feed {age(strip?.feed_age_seconds)}</span><span>Broker sync {age(strip?.sync_age_seconds)}</span>
    <span className={strip?.unresolved_incident ? 'text-negative' : ''}>{strip?.unresolved_incident ? 'Recovery / protection incident' : strip?.sync_age_seconds == null ? 'Protection not assessed' : 'No reported incident'}</span>
  </div>
}

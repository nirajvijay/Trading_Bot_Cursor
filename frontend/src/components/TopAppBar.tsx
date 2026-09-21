import {useEffect, useState} from 'react'
import {fetchSessionClock} from '../api/client'
import type { RunnerStatus, SessionCoverage } from '../api/types'

export type AppTab = 'radar' | 'checklist' | 'auth' | 'trading' | 'admin'

interface Props {
  activeTab: AppTab
  sessionDate: string
  coverage: SessionCoverage | null
  status: RunnerStatus | null
  onSessionChange: (date: string) => void
  sessions: string[]
  search: string
  onSearchChange: (value: string) => void
  username?: string
  onLogout?: () => void
}

export function TopAppBar({
  activeTab,
  sessionDate,
  coverage,
  status,
  onSessionChange,
  sessions,
  search,
  onSearchChange,
  username,
  onLogout,
}: Props) {
  const [market,setMarket] = useState('UNKNOWN')
  useEffect(() => {
    let disposed=false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {const clock=await fetchSessionClock(); if(!disposed) setMarket(clock.state)}
      catch {if(!disposed) setMarket('UNKNOWN')}
      if(!disposed) timer=setTimeout(poll,15000)
    }
    void poll();return () => {disposed=true;clearTimeout(timer)}
  },[])
  const subscribed = status?.subscribed_tokens ?? coverage?.subscribed ?? '—'

  return (
    <header className="flex justify-between items-center h-10 px-4 w-full bg-white border-b border-outline-variant shrink-0">
      <div className="flex items-center gap-4 min-w-0">
        <span className="text-sm font-extrabold uppercase tracking-tight whitespace-nowrap">
          NIFTY RADAR
        </span>
        <div className="h-4 w-px bg-outline-variant hidden sm:block" />
        <div className="hidden md:flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-1.5">
            <span className="label-caps text-on-surface-variant">Calendar session:</span>
            <span className={`label-caps flex items-center gap-1 ${market === 'OPEN' ? 'text-positive' : 'text-on-surface-variant'}`}>
              {market === 'OPEN' && <span className="w-1.5 h-1.5 rounded-full bg-positive pulse-green" />}
              {market}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="label-caps text-on-surface-variant">Subscribed:</span>
            <span className="label-caps text-primary">{subscribed}/100</span>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <span className="label-caps font-extrabold px-2.5 py-1 bg-surface-container border border-outline-variant text-on-surface-variant rounded-sm hidden sm:inline">
          Mode:{' '}
          {activeTab === 'auth'
            ? 'kite token'
            : activeTab === 'checklist'
              ? 'pre-market checks'
              : activeTab === 'trading'
                ? 'trading engine'
                : activeTab === 'admin'
                  ? 'admin console'
                  : 'observation only'}
        </span>
        {username && (
          <span className="label-caps text-on-surface-variant hidden md:inline truncate max-w-[8rem]">
            {username}
          </span>
        )}
        {activeTab === 'radar' && (
          <>
            <input
              className="bg-surface-container-low border border-outline-variant text-on-surface font-data text-[10px] w-36 lg:w-44 px-2 py-1 uppercase placeholder:text-on-surface-variant/60 label-caps"
              placeholder="Search instrument"
              value={search}
              onChange={(e) => onSearchChange(e.target.value)}
            />
            <select
              className="bg-surface-container-low border border-outline-variant text-[10px] px-2 py-1 label-caps hidden lg:block"
              value={sessionDate}
              onChange={(e) => onSessionChange(e.target.value)}
            >
              <option value={sessionDate}>{sessionDate}</option>
              {sessions
                .filter((s) => s !== sessionDate)
                .map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
            </select>
          </>
        )}
        {activeTab === 'trading' && (
          <select
            className="bg-surface-container-low border border-outline-variant text-[10px] px-2 py-1 label-caps"
            value={sessionDate}
            onChange={(e) => onSessionChange(e.target.value)}
          >
            <option value={sessionDate}>{sessionDate}</option>
            {sessions
              .filter((s) => s !== sessionDate)
              .map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
          </select>
        )}
        {onLogout && (
          <button
            type="button"
            className="label-caps px-2 py-1 border border-outline-variant text-on-surface-variant hover:bg-surface-container-low text-[10px]"
            onClick={onLogout}
            title="Log out"
          >
            Logout
          </button>
        )}
      </div>
    </header>
  )
}

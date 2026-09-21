import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { marketStatusNow } from '../lib/format'
import type { FeedStatusView, RunnerPresence } from '../lib/feedStatus'
import type { RunnerStatus, SessionCoverage } from '../api/types'
import type { AppTab } from './TopAppBar'

const TABS: { id: AppTab; label: string }[] = [
  { id: 'checklist', label: 'Checklist' },
  { id: 'radar', label: 'Radar Stream' },
  { id: 'trading', label: 'Execution Desk' },
  { id: 'admin', label: 'Diagnostics & Logs' },
  { id: 'auth', label: 'Settings' },
]

interface Props {
  activeTab: AppTab
  onTabChange: (tab: AppTab) => void
  sessionDate: string
  sessions: string[]
  onSessionChange: (date: string) => void
  search: string
  onSearchChange: (value: string) => void
  username?: string
  onLogout?: () => void
  coverage: SessionCoverage | null
  status: RunnerStatus | null
  runnerPresence: RunnerPresence
  feedStatus: FeedStatusView
  brokerAuthOk?: boolean
  checklistGateLocked?: boolean
  children: ReactNode
}

function istParts(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(now)
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  return {
    hour: Number(get('hour')),
    minute: Number(get('minute')),
    clock: `${get('hour')}:${get('minute')}:${get('second')}`,
  }
}

function marketOpensInLabel(now = new Date()): string {
  const market = marketStatusNow()
  if (market === 'OPEN') return 'MARKET OPEN (09:15–15:30 IST)'
  const { hour, minute } = istParts(now)
  const nowMins = hour * 60 + minute
  const openMins = 9 * 60 + 15
  const deltaMins = openMins - nowMins
  if (deltaMins <= 0) return 'MARKET CLOSED · NEXT OPEN 09:15 IST'
  const h = Math.floor(deltaMins / 60)
  const m = deltaMins % 60
  if (h > 0) return `MARKET OPENS IN ${h}H ${m}M (09:15:00 IST)`
  return `MARKET OPENS IN ${m}M (09:15:00 IST)`
}

export function StationConsoleShell({
  activeTab,
  onTabChange,
  sessionDate,
  sessions,
  onSessionChange,
  search,
  onSearchChange,
  username,
  onLogout,
  coverage,
  status,
  runnerPresence,
  feedStatus,
  brokerAuthOk = false,
  checklistGateLocked = true,
  children,
}: Props) {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(id)
  }, [])

  const clock = istParts(now).clock
  const marketLabel = useMemo(() => marketOpensInLabel(now), [now])
  const subscribed = status?.subscribed_tokens ?? coverage?.subscribed ?? 100
  const feedConnected =
    feedStatus.code === 'STABLE' ||
    (runnerPresence === 'running' && feedStatus.tone !== 'error')
  // Radar Stream renders its own search/sector/status toolbar (RadarHeatMap);
  // showing this bar too would duplicate it.
  const showRadarTools = false
  const showSessionSelect = activeTab === 'trading' || activeTab === 'checklist'
  const phaseLabel =
    activeTab === 'checklist'
      ? checklistGateLocked
        ? 'PRE-MARKET PHASE'
        : 'PRE-MARKET READY'
      : marketStatusNow() === 'OPEN'
        ? 'LIVE SESSION'
        : 'CONSOLE'

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#f8f9ff]">
      <header className="shrink-0 bg-white border-b border-[#e5e7eb] shadow-[0_1px_1px_rgba(0,0,0,0.04)]">
        <div className="flex items-center justify-between gap-3 px-6 py-2.5 min-h-[52px]">
          <div className="flex items-center gap-3 min-w-0 flex-wrap">
            <span className="text-[15px] font-extrabold tracking-[0.08em] uppercase text-[#0b1c30] whitespace-nowrap">
              NIFTY RADAR
            </span>
            <span className="inline-flex items-center gap-1.5 bg-[#82f5c1]/70 px-2 py-1 rounded-xl">
              <span className="size-1.5 rounded-full bg-[#006c4a]" />
              <span className="font-mono text-[10px] font-bold tracking-[0.5px] uppercase text-[#00714e]">
                {phaseLabel}
              </span>
            </span>
            <span className="hidden lg:inline font-mono text-[11px] text-[#45464d] tracking-wide">
              {marketLabel}
            </span>
          </div>

          <div className="flex items-center gap-2 sm:gap-3 shrink-0 flex-wrap justify-end">
            <span className="hidden md:inline-flex items-center gap-1.5 font-mono text-[10px] uppercase text-[#45464d]">
              <span className={`size-1.5 rounded-full ${brokerAuthOk ? 'bg-[#006c4a]' : 'bg-[#ba1a1a]'}`} />
              BROKER: KITE · {brokerAuthOk ? 'AUTH OK' : 'AUTH CHECK'}
            </span>
            <span className="hidden md:inline-flex items-center gap-1.5 font-mono text-[10px] uppercase text-[#45464d]">
              <span
                className={`size-1.5 rounded-full ${
                  feedConnected ? 'bg-[#006c4a] pulse-green' : 'bg-[#c6c6cd]'
                }`}
              />
              FEED: {feedConnected ? 'CONNECTED' : feedStatus.code}
            </span>
            <span className="hidden sm:inline font-mono text-[10px] px-2 py-0.5 border border-[#e5eeff] bg-[#eff4ff] text-[#0b1c30] rounded-[2px]">
              {runnerPresence === 'running' ? 'LIVE' : 'PAPER LIVE'}
            </span>
            <span className="font-mono text-[11px] text-[#0b1c30] tabular-nums">{clock} IST</span>
            {username && (
              <span className="hidden xl:inline-flex size-7 rounded-full bg-[#e5eeff] text-[#0b1c30] font-bold text-[10px] items-center justify-center uppercase">
                {username.slice(0, 2)}
              </span>
            )}
            {onLogout && (
              <button
                type="button"
                className="font-mono text-[10px] uppercase px-2 py-1 border border-[#e5e7eb] text-[#45464d] hover:bg-[#eff4ff] rounded-[2px]"
                onClick={onLogout}
              >
                Logout
              </button>
            )}
          </div>
        </div>

        <div className="flex items-end justify-between gap-3 px-6">
          <nav className="flex items-center gap-0 overflow-x-auto custom-scrollbar" aria-label="Station console tabs">
            {TABS.map((tab) => {
              const active = activeTab === tab.id
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => onTabChange(tab.id)}
                  className={`relative px-3.5 py-2.5 text-[12px] tracking-wide whitespace-nowrap transition-colors ${
                    active
                      ? 'text-[#005db7] font-bold'
                      : 'text-[#45464d] hover:text-[#0b1c30]'
                  }`}
                >
                  {tab.label}
                  {active && (
                    <span className="absolute left-2 right-2 bottom-0 h-0.5 bg-[#005db7] rounded-full" />
                  )}
                </button>
              )
            })}
          </nav>
          <div className="hidden sm:flex items-center gap-3 pb-2 shrink-0 font-mono text-[10px] text-[#45464d]">
            <span>UNIVERSE: {subscribed}/100</span>
            <span className="hidden lg:inline">SESSION: {sessionDate}</span>
          </div>
        </div>

        {(showRadarTools || showSessionSelect) && activeTab !== 'checklist' && (
          <div className="flex flex-wrap items-center justify-end gap-2 px-6 py-2 bg-[#eff4ff]/60 border-t border-[#e5eeff]">
            {showRadarTools && (
              <input
                className="bg-white border border-[#e5e7eb] text-[#0b1c30] font-mono text-[10px] w-36 lg:w-44 px-2 py-1 uppercase placeholder:text-[#76777d]"
                placeholder="Search instrument"
                value={search}
                onChange={(e) => onSearchChange(e.target.value)}
              />
            )}
            {showSessionSelect && (
              <select
                className="bg-white border border-[#e5e7eb] text-[10px] px-2 py-1 font-mono"
                value={sessionDate}
                onChange={(e) => onSessionChange(e.target.value)}
                aria-label="Session date"
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
          </div>
        )}

        {activeTab === 'checklist' && (
          <div className="flex justify-end px-6 py-1.5 bg-[#eff4ff]/40 border-t border-[#e5eeff]">
            <select
              className="bg-white border border-[#e5e7eb] text-[10px] px-2 py-1 font-mono rounded-[2px]"
              value={sessionDate}
              onChange={(e) => onSessionChange(e.target.value)}
              aria-label="Session date"
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
          </div>
        )}
      </header>

      <div className="flex-1 min-h-0 flex flex-col overflow-hidden">{children}</div>
    </div>
  )
}

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

const BREADCRUMB_TAIL: Record<AppTab, string> = {
  checklist: 'STATION CONSOLE',
  radar: 'RADAR STREAM',
  trading: 'EXECUTION DESK',
  admin: 'DIAGNOSTICS & LOGS',
  auth: 'SETTINGS',
}

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
    weekday: 'short',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now)
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  return {
    hour: Number(get('hour')),
    minute: Number(get('minute')),
    second: Number(get('second')),
    clock: `${get('hour')}:${get('minute')}:${get('second')}`,
  }
}

function marketOpensInLabel(now = new Date()): string {
  const market = marketStatusNow()
  if (market === 'OPEN') return 'MARKET OPEN (09:15–15:30 IST)'
  const { hour, minute } = istParts(now)
  const nowMins = hour * 60 + minute
  const openMins = 9 * 60 + 15
  let deltaMins = openMins - nowMins
  if (deltaMins <= 0) {
    // After close or weekend → next session open is not computed precisely; show closed.
    return 'MARKET CLOSED · NEXT OPEN 09:15 IST'
  }
  const h = Math.floor(deltaMins / 60)
  const m = deltaMins % 60
  if (h > 0) return `MARKET OPENS IN ${h}H ${m}M (09:15:00 IST)`
  return `MARKET OPENS IN ${m}M (09:15:00 IST)`
}

function phaseBadge(activeTab: AppTab, gateLocked: boolean): { label: string; tone: 'pre' | 'live' | 'neutral' } {
  const market = marketStatusNow()
  if (activeTab === 'checklist') {
    return {
      label: gateLocked ? 'PRE-MARKET PHASE' : market === 'OPEN' ? 'MARKET OPEN' : 'PRE-MARKET READY',
      tone: 'pre',
    }
  }
  if (activeTab === 'radar') {
    return { label: market === 'OPEN' ? 'OBSERVATION' : 'PRE-MARKET', tone: market === 'OPEN' ? 'live' : 'pre' }
  }
  if (activeTab === 'trading') return { label: 'EXECUTION', tone: 'live' }
  return { label: 'CONSOLE', tone: 'neutral' }
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
  const phase = phaseBadge(activeTab, checklistGateLocked)
  const subscribed = status?.subscribed_tokens ?? coverage?.subscribed ?? 100
  const feedConnected =
    feedStatus.code === 'STABLE' ||
    (runnerPresence === 'running' && feedStatus.tone !== 'error')
  const showRadarTools = activeTab === 'radar'
  const showSessionSelect = activeTab === 'radar' || activeTab === 'trading' || activeTab === 'checklist'

  return (
    <div className="flex flex-col h-full overflow-hidden bg-background">
      <header className="shrink-0 bg-white border-b border-outline-variant">
        <div className="flex items-center justify-between gap-3 px-4 py-2.5 min-h-[52px]">
          <div className="flex items-center gap-3 min-w-0 flex-wrap">
            <span className="text-sm font-extrabold tracking-[0.08em] uppercase text-on-surface whitespace-nowrap">
              NIFTY RADAR
            </span>
            <span
              className={`label-caps px-2 py-1 rounded-sm border font-extrabold ${
                phase.tone === 'pre'
                  ? 'bg-emerald-50 text-positive border-emerald-200'
                  : phase.tone === 'live'
                    ? 'bg-sky-50 text-primary border-sky-200'
                    : 'bg-surface-container text-on-surface-variant border-outline-variant'
              }`}
            >
              {phase.label}
            </span>
            <span className="hidden lg:inline label-caps text-on-surface-variant tracking-wide">
              {marketLabel}
            </span>
          </div>

          <div className="flex items-center gap-2 sm:gap-3 shrink-0 flex-wrap justify-end">
            <span className="hidden md:inline-flex items-center gap-1.5 label-caps text-on-surface-variant">
              <span className={`w-1.5 h-1.5 rounded-full ${brokerAuthOk ? 'bg-positive' : 'bg-negative'}`} />
              BROKER: KITE · {brokerAuthOk ? 'AUTH OK' : 'AUTH CHECK'}
            </span>
            <span className="hidden md:inline-flex items-center gap-1.5 label-caps text-on-surface-variant">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  feedConnected ? 'bg-positive pulse-green' : 'bg-outline-variant'
                }`}
              />
              FEED: {feedConnected ? 'CONNECTED' : feedStatus.code}
            </span>
            <span className="hidden sm:inline label-caps px-2 py-0.5 border border-outline-variant bg-surface-container-low text-on-surface-variant">
              {runnerPresence === 'running' ? 'LIVE' : 'PAPER LIVE'}
            </span>
            <span className="font-data text-[11px] text-on-surface tabular-nums">{clock} IST</span>
            {username && (
              <span className="hidden xl:inline label-caps text-on-surface-variant truncate max-w-[9rem]">
                {username}
              </span>
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
        </div>

        <div className="flex items-end justify-between gap-3 px-4 border-t border-outline-variant/70">
          <nav className="flex items-center gap-0 overflow-x-auto custom-scrollbar" aria-label="Station console tabs">
            {TABS.map((tab) => {
              const active = activeTab === tab.id
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => onTabChange(tab.id)}
                  className={`relative px-3.5 py-2.5 label-caps tracking-wide whitespace-nowrap transition-colors ${
                    active
                      ? 'text-primary font-extrabold'
                      : 'text-on-surface-variant hover:text-on-surface'
                  }`}
                >
                  {tab.label}
                  {active && (
                    <span className="absolute left-2 right-2 bottom-0 h-0.5 bg-primary rounded-full" />
                  )}
                </button>
              )
            })}
          </nav>
          <div className="hidden sm:flex items-center gap-3 pb-2 shrink-0">
            <span className="label-caps text-on-surface-variant">
              UNIVERSE: {subscribed}/100
            </span>
            <span className="label-caps text-on-surface-variant hidden lg:inline">
              SESSION: {sessionDate}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 bg-surface-container-low border-t border-outline-variant">
          <div className="label-caps text-on-surface-variant tracking-wider">
            DESK <span className="text-outline-variant mx-1">/</span> NIFTY 100{' '}
            <span className="text-outline-variant mx-1">/</span>{' '}
            <span className="text-on-surface">{BREADCRUMB_TAIL[activeTab]}</span>
          </div>
          <div className="flex items-center gap-2">
            {showRadarTools && (
              <input
                className="bg-white border border-outline-variant text-on-surface font-data text-[10px] w-36 lg:w-44 px-2 py-1 uppercase placeholder:text-on-surface-variant/60 label-caps"
                placeholder="Search instrument"
                value={search}
                onChange={(e) => onSearchChange(e.target.value)}
              />
            )}
            {showSessionSelect && (
              <select
                className="bg-white border border-outline-variant text-[10px] px-2 py-1 label-caps"
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
        </div>
      </header>

      <div className="flex-1 min-h-0 flex flex-col overflow-hidden">{children}</div>
    </div>
  )
}

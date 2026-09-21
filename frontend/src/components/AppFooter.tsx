import { formatTimeIst } from '../lib/format'
import { resolveFeedStatus, type RunnerPresence } from '../lib/feedStatus'
import type { RadarRow, RunnerStatus } from '../api/types'
import type { AppTab } from './TopAppBar'

interface Props {
  activeTab: AppTab
  status?: RunnerStatus | null
  runnerPresence?: RunnerPresence
  rows?: RadarRow[]
}

export function AppFooter({
  activeTab,
  status = null,
  runnerPresence = 'stopped',
  rows = [],
}: Props) {
  const now = formatTimeIst(new Date().toISOString())
  const feed = resolveFeedStatus(status, runnerPresence)

  const vwapCounts = rows.reduce(
    (acc, row) => {
      if (row.vwap_classification) acc[row.vwap_classification] = (acc[row.vwap_classification] ?? 0) + 1
      return acc
    },
    {} as Record<string, number>,
  )
  const vwapTotal = Object.values(vwapCounts).reduce((sum, n) => sum + n, 0)

  if (activeTab === 'admin') {
    return (
      <footer className="h-8 px-4 flex items-center justify-between border-t border-outline-variant bg-white text-[10px] text-on-surface-variant shrink-0">
        <div className="flex items-center gap-4 font-data">
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-primary" />
            ADMIN CONSOLE V1
          </span>
        </div>
        <span className="label-caps tracking-wider">Forward-only thresholds · canonical pause state</span>
        <div className="flex items-center gap-1.5 font-data">
          <span className="material-symbols-outlined text-[14px]">schedule</span>
          <span>{now} IST</span>
        </div>
      </footer>
    )
  }

  if (activeTab === 'trading') {
    return (
      <footer className="h-8 px-4 flex items-center justify-between border-t border-outline-variant bg-white text-[10px] text-on-surface-variant shrink-0">
        <div className="flex items-center gap-4 font-data">
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-primary" />
            TRADING ENGINE V1
          </span>
        </div>
        <span className="label-caps tracking-wider">Demo 5x unless Live Kite orders is checked</span>
        <div className="flex items-center gap-1.5 font-data">
          <span className="material-symbols-outlined text-[14px]">schedule</span>
          <span>{now} IST</span>
        </div>
      </footer>
    )
  }

  if (activeTab === 'auth') {
    return (
      <footer className="h-8 px-4 flex items-center justify-between border-t border-outline-variant bg-white text-[10px] text-on-surface-variant shrink-0">
        <div className="flex items-center gap-4 font-data">
          <span>LOCALHOST ONLY</span>
          <span className="text-outline-variant">|</span>
          <span>ENV: backend/.env</span>
        </div>
        <div className="flex items-center gap-1.5 label-caps">
          <span className="material-symbols-outlined text-[14px]">verified_user</span>
          <span>Secure environment</span>
        </div>
      </footer>
    )
  }

  if (activeTab === 'checklist') {
    return (
      <footer className="h-8 px-4 flex items-center justify-between border-t border-outline-variant bg-white text-[10px] text-on-surface-variant shrink-0">
        <div className="flex items-center gap-4 font-data">
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-primary" />
            READ-ONLY CHECKS
          </span>
        </div>
        <span className="label-caps tracking-wider">Copy commands to run locally</span>
        <div className="flex items-center gap-1.5 font-data">
          <span className="material-symbols-outlined text-[14px]">schedule</span>
          <span>{now} IST</span>
        </div>
      </footer>
    )
  }

  return (
    <footer className="h-8 px-4 flex items-center justify-between border-t border-outline-variant bg-surface-container-low text-[10px] text-on-surface-variant shrink-0">
      <div className="flex items-center gap-4 font-data">
        <span className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-positive" />
          LOCAL API CONNECTED
        </span>
      </div>
      <span className="label-caps tracking-wider">
        {activeTab === 'radar' ? feed.label : 'Observation mode enabled'}
        {activeTab === 'radar' && vwapTotal > 0 && (
          <>
            {' '}· VWAP {vwapCounts.ACCEPT ?? 0} accept · {vwapCounts.LIMITED ?? 0} limited ·{' '}
            {vwapCounts.REJECT ?? 0} reject
          </>
        )}
      </span>
      <div className="flex items-center gap-1.5 font-data">
        <span className="material-symbols-outlined text-[14px]">schedule</span>
        <span>{now} IST</span>
      </div>
    </footer>
  )
}

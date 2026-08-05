import { formatTimeIst } from '../lib/format'
import { resolveFeedStatus } from '../lib/feedStatus'
import type { RunnerStatus } from '../api/types'
import type { AppTab } from './TopAppBar'

interface Props {
  activeTab: AppTab
  status?: RunnerStatus | null
  runnerRunning?: boolean
}

export function AppFooter({ activeTab, status = null, runnerRunning = false }: Props) {
  const now = formatTimeIst(new Date().toISOString())
  const feed = resolveFeedStatus(status, runnerRunning)

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
      </span>
      <div className="flex items-center gap-1.5 font-data">
        <span className="material-symbols-outlined text-[14px]">schedule</span>
        <span>{now} IST</span>
      </div>
    </footer>
  )
}

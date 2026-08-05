import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppFooter } from './components/AppFooter'
import { AppSidebar } from './components/AppSidebar'
import { useRadarDashboard } from './hooks/useRadarDashboard'
import { usePreMarketChecklist, mergeKiteAuthStatus, computeEffectiveOverallStatus } from './hooks/usePreMarketChecklist'
import { useObservationReadiness } from './hooks/useObservationReadiness'
import { useTokenCheck } from './hooks/useTokenCheck'
import { postStartObservation } from './api/client'
import { KiteAuthPage } from './components/KiteAuthPage'
import { PreMarketChecklistPage } from './components/PreMarketChecklistPage'
import { RadarTable } from './components/RadarTable'
import { StatusStrip } from './components/StatusStrip'
import { TopAppBar, type AppTab } from './components/TopAppBar'
import { todayIst } from './lib/format'
import { FeedAlertBanner } from './components/FeedAlertBanner'
import { resolveFeedStatus } from './lib/feedStatus'
import type { RadarRow } from './api/types'

function exportCsv(rows: RadarRow[]) {
  const headers = [
    'Symbol',
    'Last 1m Close',
    '% Change',
    'Phase',
    'Direction',
    'Spike',
    'Pullback',
    'Continuation',
    'Volume',
    'Trigger Price',
    'Distance %',
    'Last Event',
    'Updated',
  ]
  const lines = rows.map((r) =>
    [
      r.symbol,
      r.last_1m_close ?? '',
      r.pct_change ?? '',
      r.phase,
      r.direction ?? '',
      r.spike,
      r.pullback,
      r.continuation,
      r.volume ?? '',
      r.trigger_price ?? '',
      r.distance_pct ?? '',
      r.last_event,
      r.updated_at ?? '',
    ].join(','),
  )
  const blob = new Blob([[headers.join(','), ...lines].join('\n')], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'nifty50-radar.csv'
  a.click()
  URL.revokeObjectURL(url)
}

export default function App() {
  const [activeTab, setActiveTab] = useState<AppTab>('radar')
  const [sessionDate, setSessionDate] = useState(todayIst())
  const [search, setSearch] = useState('')
  const radarEnabled = activeTab === 'radar'
  const { rows, coverage, status, sessions, loading, error, refresh: refreshRadar } = useRadarDashboard(
    sessionDate,
    5000,
    radarEnabled,
  )
  const {
    data: checklistData,
    loading: checklistLoading,
    error: checklistError,
    refresh: refreshChecklist,
  } = usePreMarketChecklist(sessionDate, true)
  const {
    readiness: observationReadiness,
    refresh: refreshObservationReadiness,
  } = useObservationReadiness(sessionDate, radarEnabled)
  const refreshAfterTokenCheck = useCallback(async () => {
    await Promise.all([refreshChecklist(), refreshObservationReadiness()])
  }, [refreshChecklist, refreshObservationReadiness])
  const { tokenCheck, tokenCheckedAt, tokenChecking, checkToken } = useTokenCheck(
    sessionDate,
    checklistData,
    refreshAfterTokenCheck,
  )
  const [startingObservation, setStartingObservation] = useState(false)
  const [observationError, setObservationError] = useState<string | null>(null)
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null)
  const [timelineRefreshToken, setTimelineRefreshToken] = useState(0)

  const handleRowClick = useCallback((symbol: string) => {
    setExpandedSymbol((current) => (current === symbol ? null : symbol))
  }, [])

  useEffect(() => {
    setExpandedSymbol(null)
  }, [sessionDate])

  useEffect(() => {
    if (!expandedSymbol || !radarEnabled) return
    setTimelineRefreshToken((token) => token + 1)
  }, [rows, expandedSymbol, radarEnabled])

  const handleStartObservation = useCallback(async () => {
    setStartingObservation(true)
    setObservationError(null)
    try {
      await postStartObservation(sessionDate)
      await Promise.all([refreshRadar(), refreshObservationReadiness()])
    } catch (err) {
      setObservationError(err instanceof Error ? err.message : 'Failed to start observation')
    } finally {
      setStartingObservation(false)
    }
  }, [sessionDate, refreshRadar, refreshObservationReadiness])

  const filteredRows = useMemo(() => {
    const q = search.trim().toUpperCase()
    if (!q) return rows
    return rows.filter((r) => r.symbol.includes(q))
  }, [rows, search])

  const effectiveChecklistOk = useMemo(() => {
    if (!checklistData) return false
    const kite = checklistData.areas.kite_auth
    const kiteStatus = mergeKiteAuthStatus(
      kite.status,
      tokenCheck !== null || kite.token_validated_today === true,
      tokenCheck?.valid ?? (kite.token_validated_today ? true : null),
    )
    return computeEffectiveOverallStatus(checklistData, kiteStatus) === 'ok'
  }, [checklistData, tokenCheck])

  const observationReadinessForUi = useMemo(() => {
    if (!observationReadiness) return null
    const checklistOk = effectiveChecklistOk || observationReadiness.checklist_ok
    const runnerRunning = observationReadiness.runner_running
    const marketOpen = observationReadiness.market_open
    let reason = observationReadiness.reason
    let canStart = observationReadiness.can_start
    if (!checklistOk) {
      reason = 'Complete Pre-Market Checklist first'
      canStart = false
    } else if (!marketOpen) {
      reason = 'Market closed — available 09:15–15:30 IST on weekdays'
      canStart = false
    } else if (runnerRunning) {
      reason = 'Observation runner is already running'
      canStart = false
    } else {
      reason = ''
      canStart = true
    }
    return {
      ...observationReadiness,
      checklist_ok: checklistOk,
      can_start: canStart,
      reason,
    }
  }, [observationReadiness, effectiveChecklistOk])

  const runnerRunning = observationReadinessForUi?.runner_running ?? false
  const feedStatus = resolveFeedStatus(status, runnerRunning)

  return (
    <div className="flex h-full overflow-hidden">
      <AppSidebar activeTab={activeTab} onTabChange={setActiveTab} />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <TopAppBar
          activeTab={activeTab}
          sessionDate={sessionDate}
          coverage={coverage}
          status={status}
          sessions={sessions}
          onSessionChange={setSessionDate}
          search={search}
          onSearchChange={setSearch}
        />
        <main className="flex flex-col flex-1 min-h-0 overflow-hidden">
          {activeTab === 'radar' ? (
            <>
              <StatusStrip
                coverage={coverage}
                status={status}
                observationReadiness={observationReadinessForUi}
                startingObservation={startingObservation}
                observationError={observationError}
                onStartObservation={() => void handleStartObservation()}
                onExport={() => exportCsv(filteredRows)}
              />
              <FeedAlertBanner feed={feedStatus} />
              {error && (
                <div className="mx-4 mt-2 px-3 py-2 bg-red-50 border border-red-200 text-red-800 text-sm shrink-0">
                  {error}
                </div>
              )}
              <RadarTable
                rows={filteredRows}
                loading={loading}
                sessionDate={sessionDate}
                expandedSymbol={expandedSymbol}
                onRowClick={handleRowClick}
                timelineRefreshToken={timelineRefreshToken}
              />
            </>
          ) : activeTab === 'checklist' ? (
            <PreMarketChecklistPage
              data={checklistData}
              loading={checklistLoading}
              error={checklistError}
              onRefresh={refreshChecklist}
              onGoToAuth={() => setActiveTab('auth')}
              tokenCheck={tokenCheck}
              tokenCheckedAt={tokenCheckedAt}
              tokenChecking={tokenChecking}
              onCheckToken={checkToken}
            />
          ) : (
            <KiteAuthPage />
          )}
        </main>
        <AppFooter activeTab={activeTab} status={status} runnerRunning={runnerRunning} />
      </div>
    </div>
  )
}

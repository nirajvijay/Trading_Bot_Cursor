import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppFooter } from './components/AppFooter'
import { useRadarDashboard } from './hooks/useRadarDashboard'
import { usePreMarketChecklist } from './hooks/usePreMarketChecklist'
import { useObservationReadiness } from './hooks/useObservationReadiness'
import { useTokenCheck } from './hooks/useTokenCheck'
import { ApiError, fetchMe, postKiteStart, postLogin, postLogout, postPasskeyLoginOptions, postPasskeyLoginVerify, postStartObservation, postStopObservation, setAuthHandlers } from './api/client'
import { LoginPage } from './components/LoginPage'
import { MfaSetupPage } from './components/MfaSetupPage'
import { PasskeySetupPrompt } from './components/PasskeySetupPrompt'
import { SecurityPanel } from './components/SecurityPanel'
import { getPasskey } from './lib/passkey'
import { PreMarketChecklistPage } from './components/PreMarketChecklistPage'
import { RadarHeatMap } from './components/RadarHeatMap'
import { VwapHealthPage } from './components/VwapHealthPage'
import { ExecutionDeskPage } from './components/execution/ExecutionDeskPage'
import { StationConsoleShell } from './components/StationConsoleShell'
import { type AppTab } from './components/TopAppBar'
import { todayIst } from './lib/format'
import { resolveFeedStatus, resolveRunnerPresence } from './lib/feedStatus'
import { mergeKiteAuthStatus, computeEffectiveOverallStatus } from './hooks/usePreMarketChecklist'
import type { MeResponse } from './api/types'

export default function App() {
  const [me, setMe] = useState<MeResponse | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [authChecked, setAuthChecked] = useState(false)
  const [activeTab, setActiveTab] = useState<AppTab>('radar')
  const [sessionDate, setSessionDate] = useState(todayIst())
  useEffect(() => {
    const updateDate = () => setSessionDate(todayIst())
    const timer = window.setInterval(updateDate, 60_000)
    window.addEventListener('focus', updateDate)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', updateDate) }
  }, [])
  const [search, setSearch] = useState('')
  const [showPasskeySetup, setShowPasskeySetup] = useState(false)
  const [showSecurity, setShowSecurity] = useState(false)
  const authenticated = Boolean(me)
  const radarEnabled = authenticated && activeTab === 'radar'
  const {
    rows,
    coverage,
    status,
    statusFetchOk,
    loading,
    error,
    refresh: refreshRadar,
  } = useRadarDashboard(sessionDate, 5000, radarEnabled)
  const {
    data: checklistData,
    loading: checklistLoading,
    error: checklistError,
    refresh: refreshChecklist,
  } = usePreMarketChecklist(sessionDate, authenticated, activeTab === 'checklist')
  const { readiness: observationReadiness, refresh: refreshObservationReadiness } = useObservationReadiness(
    sessionDate,
    radarEnabled,
  )
  const [startingObservation, setStartingObservation] = useState(false)
  useEffect(() => {
    if (authenticated) void refreshObservationReadiness()
  }, [authenticated, checklistData?.activity?.revision, refreshObservationReadiness])
  const [stoppingObservation, setStoppingObservation] = useState(false)
  const [observationError, setObservationError] = useState<string | null>(null)
  const refreshAfterTokenCheck = useCallback(async () => {
    await Promise.all([refreshChecklist(), refreshObservationReadiness()])
  }, [refreshChecklist, refreshObservationReadiness])
  const { tokenCheck, tokenCheckedAt, tokenChecking, checkToken } = useTokenCheck(
    sessionDate,
    checklistData,
    refreshAfterTokenCheck,
  )
  const clearSession = useCallback(() => {
    setMe(null)
  }, [])

  useEffect(() => {
    setAuthHandlers({
      onUnauthorized: () => {
        setMe(null)
      },
    })
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setAuthLoading(true)
      try {
        const data = await fetchMe()
        if (!cancelled) setMe(data)
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError && err.status === 401) {
            setMe(null)
          } else {
            setMe(null)
          }
        }
      } finally {
        if (!cancelled) {
          setAuthLoading(false)
          setAuthChecked(true)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const handleLogin = useCallback(async (username: string, password: string, totp?: string) => {
    const data = await postLogin(username, password, totp)
    setMe(data)
    setShowPasskeySetup(data.passkey_count === 0)
  }, [])

  const handlePasskeyLogin = useCallback(async () => {
    const { challenge_id, options } = await postPasskeyLoginOptions()
    const credential = await getPasskey(options)
    const data = await postPasskeyLoginVerify(challenge_id, credential)
    setMe(data)
    setShowPasskeySetup(false)
  }, [])

  const handleLogout = useCallback(async () => {
    try {
      await postLogout()
    } catch {
      // Session may already be gone.
    }
    clearSession()
  }, [clearSession])

  const filteredRows = useMemo(() => {
    const q = search.trim().toUpperCase()
    if (!q) return rows
    return rows.filter((r) => r.symbol.includes(q))
  }, [rows, search])

  const runnerPresence = resolveRunnerPresence(status, statusFetchOk)
  const feedStatus = resolveFeedStatus(status, runnerPresence)

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

  const handleStopObservation = useCallback(async () => {
    setStoppingObservation(true)
    setObservationError(null)
    try {
      await postStopObservation(sessionDate)
      await Promise.all([refreshRadar(), refreshObservationReadiness()])
    } catch (err) {
      setObservationError(err instanceof Error ? err.message : 'Failed to stop observation')
    } finally {
      setStoppingObservation(false)
    }
  }, [sessionDate, refreshRadar, refreshObservationReadiness])

  const handleChecklistRefresh = useCallback(async () => {
    await refreshChecklist()
    await refreshObservationReadiness()
  }, [refreshChecklist, refreshObservationReadiness])

  const handleStartKiteLogin = useCallback(async () => {
    const result = await postKiteStart()
    if (result.mode === 'auto' && result.success) {
      await handleChecklistRefresh()
      await checkToken()
      return result
    }
    if (result.authorize_url) window.location.assign(result.authorize_url)
    return result
  }, [handleChecklistRefresh, checkToken])

  const brokerAuthOk = useMemo(() => {
    if (!checklistData) return false
    const kiteBase = checklistData.areas.kite_auth
    const tokenValidatedFromApi = kiteBase.token_validated_today === true
    const kiteStatus = mergeKiteAuthStatus(
      kiteBase.status,
      tokenCheck !== null || tokenValidatedFromApi,
      tokenCheck?.valid ?? (tokenValidatedFromApi ? true : null),
    )
    return kiteStatus === 'ok'
  }, [checklistData, tokenCheck])

  const checklistGateLocked = useMemo(() => {
    if (!checklistData) return true
    const kiteBase = checklistData.areas.kite_auth
    const tokenValidatedFromApi = kiteBase.token_validated_today === true
    const kiteStatus = mergeKiteAuthStatus(
      kiteBase.status,
      tokenCheck !== null || tokenValidatedFromApi,
      tokenCheck?.valid ?? (tokenValidatedFromApi ? true : null),
    )
    return computeEffectiveOverallStatus(checklistData, kiteStatus) !== 'ok'
  }, [checklistData, tokenCheck])

  if (!authChecked || authLoading) {
    return (
      <div className="flex h-full items-center justify-center bg-background text-sm text-on-surface-variant">
        Checking session...
      </div>
    )
  }

  if (!authenticated) {
    return <LoginPage onLogin={handleLogin} onPasskeyLogin={handlePasskeyLogin} />
  }

  if (me && !me.mfa_enabled) {
    return (
      <MfaSetupPage
        username={me.username}
        onLogout={() => void handleLogout()}
        onCompleted={() => {
          clearSession()
        }}
      />
    )
  }

  return (
    <>
      <StationConsoleShell
        activeTab={activeTab}
        onTabChange={setActiveTab}
        sessionDate={sessionDate}
        search={search}
        onSearchChange={setSearch}
        username={me?.username}
        onLogout={() => void handleLogout()}
        runnerPresence={runnerPresence}
        feedStatus={feedStatus}
        brokerAuthOk={brokerAuthOk}
        checklistGateLocked={checklistGateLocked}
        onOpenSecurity={() => setShowSecurity(true)}
      >
      <main className="flex flex-col flex-1 min-h-0 overflow-hidden">
        {activeTab === 'radar' ? (
          <>
            {error && (
              <div className="mx-4 mt-2 px-3 py-2 bg-red-50 border border-red-200 text-red-800 text-sm shrink-0">
                {error}
              </div>
            )}
            <RadarHeatMap
              rows={filteredRows}
              sessionTriggered={coverage?.continuation_successful}
              sessionVwapSuccessful={coverage?.vwap_successful}
              sessionRejected={coverage?.continuation_failed}
              loading={loading}
              sessionDate={sessionDate}
              search=""
              observationReadiness={observationReadiness}
              runnerPresence={runnerPresence}
              startingObservation={startingObservation}
              stoppingObservation={stoppingObservation}
              observationError={observationError}
              onStartObservation={() => void handleStartObservation()}
              onStopObservation={() => void handleStopObservation()}
            />
          </>
        ) : activeTab === 'checklist' ? (
          <PreMarketChecklistPage
            data={checklistData}
            loading={checklistLoading}
            error={checklistError}
            onRefresh={handleChecklistRefresh}
            onStartKiteLogin={handleStartKiteLogin}
            tokenCheck={tokenCheck}
            tokenCheckedAt={tokenCheckedAt}
            tokenChecking={tokenChecking}
            onCheckToken={checkToken}
            onNavigateToObservation={() => setActiveTab('radar')}
          />
        ) : activeTab === 'trading' ? (
          <ExecutionDeskPage sessionDate={sessionDate} />
        ) : (
          <VwapHealthPage />
        )}
      </main>
      {activeTab !== 'checklist' && (
        <AppFooter activeTab={activeTab} status={status} runnerPresence={runnerPresence} rows={rows} />
      )}
      </StationConsoleShell>
      {showPasskeySetup && <PasskeySetupPrompt onDone={() => setShowPasskeySetup(false)} />}
      {showSecurity && (
        <SecurityPanel
          onClose={() => setShowSecurity(false)}
          onPasskeyCountChange={(count) => {
            // Keep the first-run prompt in step with what the panel just did,
            // so removing the last passkey re-offers setup at the next login
            // instead of leaving it unreachable.
            setMe((current) => (current ? { ...current, passkey_count: count } : current))
          }}
        />
      )}
    </>
  )
}

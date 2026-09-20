import { useCallback, useEffect, useMemo, useState } from 'react'
import { postGenerateLocalData } from '../api/client'
import type { CheckTokenResponse, ChecklistStatus, PreMarketChecklistResponse } from '../api/types'
import { formatDateTimeIst } from '../lib/format'
import {
  mergeKiteAuthStatus,
  computeEffectiveOverallStatus,
  effectiveNextStep,
} from '../hooks/usePreMarketChecklist'
import {
  ChecklistStage,
  StageMetricCard,
  shouldExpandByDefault,
} from './checklist/ChecklistStage'

type StageId = 'kite' | 'instruments' | 'historical' | 'baselines' | 'five_minute'

interface Props {
  data: PreMarketChecklistResponse | null
  loading: boolean
  error: string | null
  onRefresh: () => void | Promise<void>
  onGoToAuth: () => void
  tokenCheck: CheckTokenResponse | null
  tokenCheckedAt: string | null
  tokenChecking: boolean
  onCheckToken: () => Promise<CheckTokenResponse>
}

function isOk(status: ChecklistStatus): boolean {
  return status === 'ok'
}

function isBlocked(status: ChecklistStatus): boolean {
  return status === 'failed' || status === 'needs_update'
}

function clockNow(): string {
  return new Date().toLocaleTimeString('en-IN', {
    hour12: false,
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function ValidBadge({ label = 'VALID' }: { label?: string }) {
  return (
    <span className="bg-[#82f5c1] inline-flex gap-1 items-center px-1.5 py-0.5 rounded-[2px]">
      <span className="size-1.5 rounded-full bg-[#006c4a]" />
      <span className="font-mono text-[10px] font-semibold uppercase text-[#00714e] leading-[15px]">
        {label}
      </span>
    </span>
  )
}

function InvalidBadge({ label = 'INVALID' }: { label?: string }) {
  return (
    <span className="bg-[#ffdad6] inline-flex gap-1 items-center px-1.5 py-0.5 rounded-[2px]">
      <span className="size-1.5 rounded-full bg-[#ba1a1a]" />
      <span className="font-mono text-[10px] font-semibold uppercase text-[#93000a] leading-[15px]">
        {label}
      </span>
    </span>
  )
}

export function PreMarketChecklistPage({
  data,
  loading,
  error,
  onRefresh,
  onGoToAuth,
  tokenCheck,
  tokenCheckedAt,
  tokenChecking,
  onCheckToken,
}: Props) {
  const [generatingTask, setGeneratingTask] = useState<string | null>(null)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [runningAll, setRunningAll] = useState(false)
  const [expanded, setExpanded] = useState<Record<StageId, boolean> | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [cliLines, setCliLines] = useState<string[]>([])
  const [syncClock, setSyncClock] = useState(clockNow)

  useEffect(() => {
    const id = window.setInterval(() => setSyncClock(clockNow()), 1000)
    return () => window.clearInterval(id)
  }, [])

  const handleGenerate = useCallback(
    async (task: string) => {
      setGeneratingTask(task)
      setGenerateError(null)
      try {
        const result = await postGenerateLocalData(task, data?.session_date)
        setCliLines((prev) => [
          ...prev.slice(-50),
          `[${clockNow()}] [GENERATE:${task}] ${result.message}`,
        ])
        await onRefresh()
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Generation failed'
        setGenerateError(msg)
        setCliLines((prev) => [
          ...prev.slice(-50),
          `[${clockNow()}] [GENERATE:${task}] ERR ${msg}`,
        ])
      } finally {
        setGeneratingTask(null)
      }
    },
    [onRefresh, data?.session_date],
  )

  const handleCheckToken = useCallback(async () => {
    const result = await onCheckToken()
    setCliLines((prev) => [
      ...prev.slice(-50),
      `[${clockNow()}] [KITE] ${result.message ?? (result.valid ? 'Token valid' : 'Token invalid')}`,
    ])
  }, [onCheckToken])

  useEffect(() => {
    if (!data) return
    const kiteBase = data.areas.kite_auth
    const tokenValidatedFromApi = kiteBase.token_validated_today === true
    const kiteStatus = mergeKiteAuthStatus(
      kiteBase.status,
      tokenCheck !== null || tokenValidatedFromApi,
      tokenCheck?.valid ?? (tokenValidatedFromApi ? true : null),
    )
    setExpanded((prev) => {
      if (prev) return prev
      return {
        kite: shouldExpandByDefault(kiteStatus),
        instruments: shouldExpandByDefault(data.areas.instruments.status),
        historical: shouldExpandByDefault(data.areas.historical_candles.status),
        baselines: shouldExpandByDefault(data.areas.baselines.status),
        five_minute: shouldExpandByDefault(data.areas.five_minute_candles.status),
      }
    })
  }, [data, tokenCheck])

  useEffect(() => {
    if (!data) return
    const stamp = clockNow()
    setCliLines([
      `[${stamp}] [CHECKLIST] session=${data.session_date} overall=${data.overall_status}`,
      ...data.blockers.slice(0, 8).map((b) => `[${stamp}] [BLOCKER] ${b}`),
      `[${stamp}] [NEXT] ${data.next_step}`,
      `[${stamp}] [RUNNER] ${data.suggested_commands.runner}`,
    ])
  }, [data?.session_date, data?.checked_at, data?.overall_status])

  useEffect(() => {
    if (!autoRefresh) return
    const id = window.setInterval(() => {
      void onRefresh()
    }, 5000)
    return () => window.clearInterval(id)
  }, [autoRefresh, onRefresh])

  const toggleStage = useCallback((id: StageId) => {
    setExpanded((prev) => {
      const base =
        prev ??
        ({
          kite: false,
          instruments: false,
          historical: false,
          baselines: false,
          five_minute: false,
        } as Record<StageId, boolean>)
      return { ...base, [id]: !base[id] }
    })
  }, [])

  const focusStage = useCallback((id: StageId) => {
    setExpanded((prev) => {
      const base =
        prev ??
        ({
          kite: false,
          instruments: false,
          historical: false,
          baselines: false,
          five_minute: false,
        } as Record<StageId, boolean>)
      return { ...base, [id]: true }
    })
  }, [])

  const handleRunAllPending = useCallback(async () => {
    if (!data) return
    setRunningAll(true)
    setGenerateError(null)
    try {
      const kiteBase = data.areas.kite_auth
      const tokenValidatedFromApi = kiteBase.token_validated_today === true
      const kiteStatus = mergeKiteAuthStatus(
        kiteBase.status,
        tokenCheck !== null || tokenValidatedFromApi,
        tokenCheck?.valid ?? (tokenValidatedFromApi ? true : null),
      )
      if (kiteStatus !== 'ok') await handleCheckToken()
      const jobs = [
        { task: 'instruments', status: data.areas.instruments.status, enabled: Boolean(data.areas.instruments.generate_action) },
        { task: 'historical', status: data.areas.historical_candles.status, enabled: Boolean(data.areas.historical_candles.generate_action) },
        { task: 'baselines', status: data.areas.baselines.status, enabled: Boolean(data.areas.baselines.generate_action) },
        { task: 'five-minute', status: data.areas.five_minute_candles.status, enabled: Boolean(data.areas.five_minute_candles.generate_action) },
      ]
      for (const job of jobs) {
        if (job.enabled && job.status !== 'ok') await handleGenerate(job.task)
      }
      await onRefresh()
    } finally {
      setRunningAll(false)
    }
  }, [data, tokenCheck, handleCheckToken, handleGenerate, onRefresh])

  const derived = useMemo(() => {
    if (!data) return null
    const kiteBase = data.areas.kite_auth
    const tokenValidatedFromApi = kiteBase.token_validated_today === true
    const kiteStatus = mergeKiteAuthStatus(
      kiteBase.status,
      tokenCheck !== null || tokenValidatedFromApi,
      tokenCheck?.valid ?? (tokenValidatedFromApi ? true : null),
    )
    const kiteValid =
      tokenCheck === null && !tokenValidatedFromApi
        ? null
        : (tokenCheck?.valid ?? tokenValidatedFromApi)
    const stageStatuses = [
      { id: 'kite' as const, status: kiteStatus, label: 'KITE AUTH', short: 'KITE AUTH' },
      { id: 'instruments' as const, status: data.areas.instruments.status, label: 'INSTRUMENTS', short: 'INSTRUMENTS' },
      { id: 'historical' as const, status: data.areas.historical_candles.status, label: 'GENERATE 1 MINUTE CANDLE', short: '1M CANDLES' },
      { id: 'baselines' as const, status: data.areas.baselines.status, label: 'BASELINES', short: 'BASELINES' },
      { id: 'five_minute' as const, status: data.areas.five_minute_candles.status, label: 'GENERATE 5 MIN CANDLES', short: '5M CANDLES' },
    ]
    return { kiteBase, kiteStatus, kiteValid, stageStatuses }
  }, [data, tokenCheck])

  if (loading && !data) {
    return (
      <div className="flex-1 flex items-center justify-center text-[#45464d] text-sm bg-[#f8f9ff]">
        Loading pre-market checklist…
      </div>
    )
  }

  if (!data || !derived) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-sm bg-[#f8f9ff]">
        {error && (
          <div className="px-3 py-2 bg-[#ffdad6]/50 border border-[#ffdad6] text-[#ba1a1a] max-w-lg">
            {error}
          </div>
        )}
        <button
          type="button"
          onClick={() => void onRefresh()}
          className="px-3 py-1.5 bg-black text-white rounded-[2px] text-[12px] font-semibold"
        >
          Retry
        </button>
      </div>
    )
  }

  const { kiteBase, kiteStatus, kiteValid, stageStatuses } = derived
  const completedCount = stageStatuses.filter((s) => isOk(s.status)).length
  const blockedCount = stageStatuses.filter((s) => isBlocked(s.status)).length
  const lockedCount = stageStatuses.length - completedCount - blockedCount
  const progressPct = Math.round((completedCount / stageStatuses.length) * 100)
  const effectiveOverall = computeEffectiveOverallStatus(data, kiteStatus)
  const overallReady = effectiveOverall === 'ok'
  const gateLocked = !overallReady
  const displayNextStep = effectiveNextStep(data, effectiveOverall)
  const open = expanded ?? {
    kite: shouldExpandByDefault(kiteStatus),
    instruments: shouldExpandByDefault(data.areas.instruments.status),
    historical: shouldExpandByDefault(data.areas.historical_candles.status),
    baselines: shouldExpandByDefault(data.areas.baselines.status),
    five_minute: shouldExpandByDefault(data.areas.five_minute_candles.status),
  }

  const instruments = data.areas.instruments
  const universePct =
    instruments.expected_count > 0
      ? Math.round((instruments.instruments_count / instruments.expected_count) * 100)
      : 0

  const blockerStage = stageStatuses.find((s) => isBlocked(s.status))
  const baselines = data.areas.baselines
  const showCritical = gateLocked && (baselines.status !== 'ok' || data.blockers.length > 0)

  const kiteBadge =
    kiteStatus === 'ok'
      ? 'ACTIVE & AUTHENTICATED'
      : kiteStatus === 'failed'
        ? 'AUTH FAILED'
        : 'PENDING VALIDATION'

  const instrumentsBadge =
    instruments.status === 'ok' ? 'SYNCED & COMPLETE' : instruments.status === 'failed' ? 'SYNC FAILED' : 'NEEDS UPDATE'

  const histBadge =
    data.areas.historical_candles.status === 'ok'
      ? 'VALID (NOT EXPIRED)'
      : data.areas.historical_candles.status === 'failed'
        ? 'BLOCKED'
        : 'NEEDS UPDATE'

  const baseBadge =
    baselines.status === 'ok'
      ? 'VALID'
      : baselines.status === 'failed'
        ? 'BLOCKED: MISSING FOR TODAY'
        : 'INVALID (MISSING / STALE)'

  const fiveBadge =
    data.areas.five_minute_candles.status === 'ok'
      ? 'SEEDED'
      : data.areas.five_minute_candles.status === 'failed'
        ? 'BLOCKED'
        : 'PENDING: WAITING ON PRIOR STAGE'

  return (
    <div className="flex-1 overflow-y-auto custom-scrollbar bg-[#f8f9ff] px-6 py-3 space-y-4">
      {/* Inline breadcrumb (Figma) */}
      <div className="flex gap-1 items-center font-mono text-[10px] leading-3 text-[#45464d]">
        <span>DESK</span>
        <span>/</span>
        <span>NIFTY 100</span>
        <span>/</span>
        <span className="font-semibold uppercase text-[#0b1c30]">STATION CONSOLE</span>
      </div>

      {error && (
        <div className="px-3 py-2 bg-[#ffdad6]/40 border border-[#ffdad6] text-[#ba1a1a] text-sm">
          {error}
        </div>
      )}
      {generateError && (
        <div className="px-3 py-2 bg-[#ffdad6]/40 border border-[#ffdad6] text-[#ba1a1a] text-sm whitespace-pre-wrap">
          {generateError}
        </div>
      )}

      {/* TOP CONSOLE / PIPELINE GATE HEADER */}
      <section className="bg-white border border-[#e5e7eb] drop-shadow-[0px_1px_1px_rgba(0,0,0,0.05)] flex flex-col gap-3 p-[17px] rounded-[4px]">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex flex-col gap-1 min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <h1 className="font-bold text-[22px] leading-7 tracking-[-0.55px] text-[#0b1c30]">
                MORNING PRE-MARKET CHECKLIST
              </h1>
              <span className="bg-[rgba(255,218,214,0.5)] border border-[rgba(255,218,214,0.5)] inline-flex gap-1.5 items-center px-[7px] py-[5px] rounded-xl shadow-sm">
                <span className="relative size-2">
                  <span className="absolute inset-0 rounded-xl bg-[#ba1a1a] opacity-75" />
                  <span className="relative block size-2 rounded-xl bg-[#ba1a1a]" />
                </span>
                <img src="/figma/icon-1.svg" alt="" className="h-[13px] w-2.5" />
                <span className="font-mono text-[10px] font-bold tracking-[0.5px] uppercase text-[#ba1a1a] leading-3">
                  {gateLocked ? 'GATE LOCKED (INCOMPLETE)' : 'GATE OPEN'}
                </span>
              </span>
            </div>
            <p className="text-[13px] leading-[18px] text-[#45464d]">
              Automated 5-stage pre-session verification for NIFTY 100 live feed and algorithmic
              execution engine
            </p>
          </div>
          <div className="flex gap-1 items-center shrink-0">
            <button
              type="button"
              onClick={() => void handleRunAllPending()}
              disabled={runningAll || loading || overallReady}
              className="bg-black inline-flex gap-1 items-center px-3 py-1 rounded-[2px] text-white text-[15px] font-semibold leading-5 disabled:opacity-50"
            >
              <img src="/figma/icon-2.svg" alt="" className="h-[9px] w-[7px]" />
              {runningAll ? 'Running…' : 'Run All Pending Checks'}
            </button>
            <button
              type="button"
              onClick={() => void onRefresh()}
              disabled={loading}
              className="bg-[#eff4ff] inline-flex items-center px-1.5 py-1 rounded-[2px] disabled:opacity-50"
              title="Refresh"
            >
              <img src="/figma/icon-3.svg" alt="" className="size-[11px]" />
            </button>
          </div>
        </div>

        {/* Progress */}
        <div className="bg-[#eff4ff] rounded-[2px] p-1.5 flex flex-col gap-1">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div className="flex gap-1 items-center font-mono text-[11px] leading-[14px]">
              <span className="font-bold text-[#0b1c30]">PIPELINE VERIFICATION:</span>
              <span className="font-semibold text-[#006c4a]">
                {completedCount} of {stageStatuses.length} Stages Complete ({progressPct}%)
              </span>
            </div>
            <div className="flex gap-3 items-center font-mono text-[11px] text-[#45464d]">
              <span className="inline-flex gap-1 items-center">
                <span className="size-2 rounded-full bg-[#006c4a]" />
                {completedCount} Passed
              </span>
              <span className="inline-flex gap-1 items-center">
                <span className="size-2 rounded-full bg-[#ba1a1a]" />
                {blockedCount} Blocked
              </span>
              <span className="inline-flex gap-1 items-center">
                <span className="size-2 rounded-full bg-[#c6c6cd]" />
                {lockedCount} Locked
              </span>
            </div>
          </div>
          <div className="bg-[#e5eeff] flex gap-0.5 h-2 items-stretch overflow-hidden p-0.5 rounded-xl w-full">
            {stageStatuses.map((s, idx) => (
              <div
                key={s.id}
                className={`flex-1 h-full ${
                  s.status === 'ok'
                    ? 'bg-[#006c4a]'
                    : isBlocked(s.status)
                      ? 'bg-[#ba1a1a]'
                      : 'bg-[#c6c6cd]'
                } ${idx === 0 ? 'rounded-l-[2px]' : ''} ${
                  idx === stageStatuses.length - 1 ? 'rounded-r-[2px]' : ''
                }`}
                title={`${s.label}: ${s.status}`}
              />
            ))}
          </div>
        </div>

        {/* 4 metric cards */}
        <div className="flex gap-3 items-stretch justify-center flex-wrap lg:flex-nowrap">
          <div className="bg-white border border-[#e5eeff] drop-shadow-sm flex flex-1 flex-col justify-between min-w-[180px] p-[13px] rounded-[4px]">
            <p className="font-mono text-[10px] font-semibold tracking-[0.5px] uppercase text-[#45464d] leading-3">
              CURRENT TRADING DAY
            </p>
            <p className="font-mono text-[13px] font-bold text-[#0b1c30] leading-[18px] py-1">
              {data.session_date}
            </p>
            <div className="border-t border-[#e5eeff] pt-[5px]">
              <p className="text-[11px] leading-[16.5px] text-[#45464d]">Active NSE Market Session</p>
            </div>
          </div>

          <div className="bg-white border border-[#e5eeff] drop-shadow-sm flex flex-1 flex-col justify-between min-w-[180px] p-[13px] rounded-[4px]">
            <div className="flex items-center justify-between pb-1">
              <p className="font-mono text-[10px] font-semibold tracking-[0.5px] uppercase text-[#45464d] leading-3">
                UNIVERSE COVERAGE
              </p>
              <span className="bg-[#e5eeff] px-1.5 py-0.5 rounded-[2px] font-mono text-[10px] font-semibold text-[#0b1c30] leading-[15px]">
                {universePct}% SYNC
              </span>
            </div>
            <p className="font-mono text-[13px] font-bold text-[#006c4a] leading-[18px] py-1">
              {instruments.instruments_count} / {instruments.expected_count}
            </p>
            <div className="border-t border-[#e5eeff] pt-[5px]">
              <p className="text-[11px] leading-[16.5px] text-[#45464d]">
                NIFTY 100 Constituents Mapped
              </p>
            </div>
          </div>

          <div
            className={`flex flex-1 flex-col justify-between min-w-[180px] p-[13px] rounded-[4px] shadow-sm border ${
              gateLocked
                ? 'bg-[rgba(255,218,214,0.3)] border-[rgba(255,218,214,0.5)]'
                : 'bg-white border-[#e5eeff]'
            }`}
          >
            <div className="flex items-center justify-between pb-1">
              <p
                className={`font-mono text-[10px] font-bold tracking-[0.5px] uppercase leading-3 ${
                  gateLocked ? 'text-[#93000a]' : 'text-[#45464d]'
                }`}
              >
                PIPELINE BLOCKER
              </p>
              <span
                className={`px-1.5 py-0.5 rounded-[2px] font-mono text-[10px] font-semibold uppercase leading-[15px] ${
                  gateLocked ? 'bg-[#ba1a1a] text-white' : 'bg-[#82f5c1] text-[#00714e]'
                }`}
              >
                {gateLocked ? 'HALTED' : 'CLEAR'}
              </span>
            </div>
            <p
              className={`font-mono text-[13px] font-bold leading-[18px] py-1 ${
                gateLocked ? 'text-[#ba1a1a]' : 'text-[#006c4a]'
              }`}
            >
              {gateLocked ? blockerStage?.short ?? 'PIPELINE' : 'NONE'}
            </p>
            <div
              className={`border-t pt-[5px] ${
                gateLocked ? 'border-[rgba(255,218,214,0.5)]' : 'border-[#e5eeff]'
              }`}
            >
              <p
                className={`text-[11px] leading-[16.5px] ${
                  gateLocked ? 'text-[#ba1a1a]' : 'text-[#45464d]'
                }`}
              >
                {gateLocked ? displayNextStep : 'All stages clear'}
              </p>
            </div>
          </div>

          <div
            className={`flex flex-1 flex-col justify-between min-w-[180px] p-[13px] rounded-[4px] shadow-sm border ${
              overallReady
                ? 'bg-white border-[#e5eeff]'
                : 'bg-[rgba(255,218,214,0.5)] border-[rgba(255,218,214,0.5)]'
            }`}
          >
            <div className="border-b border-[rgba(255,218,214,0.5)] pb-[5px] flex items-center justify-between">
              <span className="inline-flex gap-1.5 items-center">
                <span className="size-2 rounded-full bg-[#ba1a1a]" />
                <span className="font-mono text-[10px] font-bold uppercase text-[#93000a] leading-3">
                  {overallReady ? 'READY' : 'BLOCKED'}
                </span>
              </span>
              <span className="bg-[#ba1a1a] drop-shadow-sm inline-flex gap-1 items-center px-1.5 py-0.5 rounded-[2px]">
                <img src="/figma/icon-4.svg" alt="" className="h-[10px] w-2" />
                <span className="font-mono text-[10px] font-bold uppercase text-white leading-[15px]">
                  {data.areas.dashboard_readiness.market_hour_trial_ready ? 'READY' : 'NOT READY'}
                </span>
              </span>
            </div>
            <p className="font-mono text-[13px] font-bold tracking-[-0.325px] text-[#ba1a1a] leading-[18px] py-1">
              START OBSERVATION
            </p>
            <div className="border-t border-[rgba(255,218,214,0.5)] pt-[5px] flex gap-1 items-center">
              <img src="/figma/icon-5.svg" alt="" className="size-[11px]" />
              <p className="text-[11px] font-medium leading-[16.5px] text-[#ba1a1a]">
                {data.areas.dashboard_readiness.trial_ready_reason ||
                  'All 5 stages required to enable observation'}
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Critical banner */}
      {showCritical && (
        <section className="bg-[rgba(255,218,214,0.3)] border border-[rgba(255,218,214,0.5)] flex items-center justify-between gap-3 p-[13px] rounded-[4px] shadow-sm flex-wrap">
          <div className="flex gap-3 items-start min-w-0">
            <div className="bg-[#ba1a1a] drop-shadow-sm flex items-center justify-center rounded-[2px] size-9 shrink-0">
              <img src="/figma/icon-6.svg" alt="" className="size-[17px]" />
            </div>
            <div className="flex flex-col gap-1 min-w-0">
              <div className="flex gap-1 items-center flex-wrap">
                <p className="font-bold text-[15px] tracking-[-0.375px] text-[#ba1a1a] leading-5">
                  Critical Readiness Blocker:{' '}
                  {baselines.status !== 'ok'
                    ? `Baselines Missing for ${data.session_date}`
                    : data.blockers[0] ?? 'Pipeline incomplete'}
                </p>
                <span className="bg-[#ba1a1a] inline-flex gap-1 items-center px-1.5 py-0.5 rounded-[2px]">
                  <span className="size-1.5 rounded-full bg-white" />
                  <span className="font-mono text-[10px] font-bold uppercase text-white leading-[15px]">
                    CRITICAL PREREQUISITE
                  </span>
                </span>
                <span className="bg-[#ffdad6] border border-[rgba(255,218,214,0.5)] px-[7px] py-[3px] rounded-[2px] font-mono text-[10px] font-bold uppercase text-[#93000a] leading-[15px]">
                  PIPELINE HALTED
                </span>
              </div>
              <p className="text-[12px] leading-[18px] text-[#93000a]">
                Baselines expected as of{' '}
                <span className="inline-block bg-[rgba(255,218,214,0.5)] px-1 rounded-[2px] font-mono font-bold text-[#ba1a1a]">
                  {baselines.expected_as_of ?? data.session_date}
                </span>
                ; latest recorded baseline vector partition is{' '}
                <span className="inline-block bg-[rgba(255,218,214,0.5)] px-1 rounded-[2px] font-mono font-bold text-[#ba1a1a]">
                  {baselines.baseline_as_of ?? '—'}
                </span>{' '}
                ({baselines.symbols_covered}/{baselines.expected_count} symbols ready).{' '}
                {baselines.message || 'Algorithmic execution gate locked.'}
              </p>
            </div>
          </div>
          <div className="flex gap-1 items-center shrink-0">
            {baselines.generate_action && baselines.status !== 'ok' && (
              <button
                type="button"
                onClick={() => void handleGenerate('baselines')}
                disabled={generatingTask === 'baselines'}
                className="bg-[#ba1a1a] drop-shadow-sm inline-flex gap-1 items-center px-3 py-1 rounded-[2px] text-white text-[15px] font-bold leading-5 disabled:opacity-50"
              >
                <img src="/figma/icon-7.svg" alt="" className="h-[13px] w-[11px]" />
                {generatingTask === 'baselines' ? 'Generating…' : 'Execute: Generate Baselines Now'}
              </button>
            )}
            <button
              type="button"
              onClick={() => focusStage('baselines')}
              className="bg-white border border-[rgba(255,218,214,0.5)] drop-shadow-sm px-[7px] py-[5px] rounded-[2px] text-[#ba1a1a] text-[15px] font-semibold leading-5"
            >
              View Run Log
            </button>
          </div>
        </section>
      )}

      {/* Dual column */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
        <div className="xl:col-span-8 flex flex-col gap-3 min-w-0">
          <div className="flex items-center justify-between gap-2 pb-1 flex-wrap">
            <div className="flex gap-1 items-center flex-wrap">
              <img src="/figma/icon-8.svg" alt="" className="h-[14px] w-[15px]" />
              <h2 className="font-bold text-[18px] leading-6 text-[#0b1c30]">
                Execution Gate Milestones
              </h2>
              <span className="bg-[#e5eeff] ml-1 px-1.5 py-0.5 rounded-[2px] font-mono text-[10px] font-semibold uppercase text-[#0b1c30] leading-3">
                5 PIPELINE GATES
              </span>
              {blockedCount > 0 && (
                <span className="bg-[#ffdad6] border border-[rgba(255,218,214,0.5)] inline-flex gap-1 items-center px-[7px] py-[3px] rounded-[2px]">
                  <span className="size-1.5 rounded-full bg-[#ba1a1a]" />
                  <span className="font-mono text-[10px] font-bold uppercase text-[#93000a] leading-[15px]">
                    {blockedCount} GATES BLOCKED
                  </span>
                </span>
              )}
            </div>
            <p className="font-mono text-[10px] text-[#45464d] leading-3">
              SYNC CLOCK:{' '}
              <span className="font-bold text-[#0b1c30]">{syncClock}.000 IST</span>
            </p>
          </div>

          {/* Stage 01 */}
          <ChecklistStage
            stageNumber="STAGE 01"
            title="KITE AUTH"
            status={kiteStatus}
            badgeLabel={kiteBadge}
            expanded={open.kite}
            onToggle={() => toggleStage('kite')}
            secondaryAction={{
              label: 'Check Auth Status',
              onClick: () => void handleCheckToken(),
              loading: tokenChecking,
              iconSrc: '/figma/icon-10.svg',
            }}
            primaryAction={{
              label: 'Generate Kite Token',
              onClick: onGoToAuth,
              variant: 'primary',
              iconSrc: '/figma/icon-11.svg',
            }}
          >
            <StageMetricCard
              label="SESSION STATUS"
              badge={kiteStatus === 'ok' ? <ValidBadge /> : <InvalidBadge label="CHECK" />}
            >
              <p className="font-bold text-[13px] leading-[19.5px] text-[#0b1c30]">
                {kiteValid === true
                  ? 'Authenticated & Active'
                  : kiteValid === false
                    ? 'Invalid Token'
                    : 'Not Validated'}
              </p>
              <p className="text-[11px] leading-[16.5px] text-[#45464d]">
                Broker: Zerodha Kite Connect v3 •{' '}
                {kiteBase.access_token_present ? 'Token Present' : 'Token Missing'}
              </p>
            </StageMetricCard>
            <StageMetricCard
              label="TIMESTAMPS"
              badge={
                <span className="inline-flex gap-1 items-center font-mono text-[10px] text-[#45464d]">
                  <img src="/figma/icon-12.svg" alt="" className="size-[12px]" />
                  IST
                </span>
              }
            >
              <div className="flex justify-between w-full text-[11px]">
                <span className="text-[#45464d]">Last Checked:</span>
                <span className="font-mono font-semibold text-[#0b1c30]">
                  {tokenCheckedAt
                    ? formatDateTimeIst(tokenCheckedAt)
                    : kiteBase.token_checked_at
                      ? formatDateTimeIst(kiteBase.token_checked_at)
                      : '—'}
                </span>
              </div>
            </StageMetricCard>
            <StageMetricCard label="TOKEN PREVIEW">
              <p className="font-mono text-[12px] font-semibold text-[#0b1c30] break-all">
                {kiteBase.masked_access_token ?? (kiteBase.access_token_present ? '••••••••' : '—')}
              </p>
              {kiteBase.copy_command && (
                <button
                  type="button"
                  className="text-[11px] text-[#005db7] underline mt-1"
                  onClick={() => void navigator.clipboard.writeText(kiteBase.copy_command)}
                >
                  Copy check command
                </button>
              )}
            </StageMetricCard>
          </ChecklistStage>

          {/* Stage 02 */}
          <ChecklistStage
            stageNumber="STAGE 02"
            title="INSTRUMENTS"
            status={instruments.status}
            badgeLabel={instrumentsBadge}
            expanded={open.instruments}
            onToggle={() => toggleStage('instruments')}
            secondaryAction={{
              label: 'Check Instruments',
              onClick: () => void onRefresh(),
              iconSrc: '/figma/icon-13.svg',
            }}
            primaryAction={
              instruments.generate_action
                ? {
                    label: instruments.generate_action.label || 'Generate Instruments',
                    onClick: () => void handleGenerate('instruments'),
                    variant: 'primary',
                    loading: generatingTask === 'instruments',
                    iconSrc: '/figma/icon-14.svg',
                  }
                : undefined
            }
          >
            <StageMetricCard
              label="UNIVERSE"
              badge={instruments.status === 'ok' ? <ValidBadge label="SYNCED" /> : <InvalidBadge />}
            >
              <p className="font-bold text-[13px] text-[#0b1c30]">
                {instruments.instruments_count} / {instruments.expected_count}
              </p>
              <p className="text-[11px] text-[#45464d]">
                Tick-size {instruments.tick_size_count}/{instruments.expected_count}
              </p>
            </StageMetricCard>
            <StageMetricCard label="LAST UPDATED">
              <p className="font-mono text-[12px] font-semibold text-[#0b1c30]">
                {formatDateTimeIst(instruments.last_updated)}
              </p>
            </StageMetricCard>
            <StageMetricCard label="STATUS">
              <p className="text-[13px] font-bold text-[#0b1c30]">
                {instruments.status === 'ok' ? 'Up to Date' : instruments.message}
              </p>
            </StageMetricCard>
          </ChecklistStage>

          {/* Stage 03 */}
          <ChecklistStage
            stageNumber="STAGE 03"
            title="GENERATE 1 MINUTE CANDLES"
            status={data.areas.historical_candles.status}
            badgeLabel={histBadge}
            expanded={open.historical}
            onToggle={() => toggleStage('historical')}
            secondaryAction={{
              label: 'Check Candles',
              onClick: () => void onRefresh(),
            }}
            primaryAction={
              data.areas.historical_candles.generate_action
                ? {
                    label: data.areas.historical_candles.generate_action.label,
                    onClick: () => void handleGenerate('historical'),
                    variant: data.areas.historical_candles.status === 'ok' ? 'secondary' : 'primary',
                    loading: generatingTask === 'historical',
                  }
                : undefined
            }
          >
            <StageMetricCard
              label="CANDLE STATUS"
              badge={
                data.areas.historical_candles.status === 'ok' ? (
                  <ValidBadge />
                ) : (
                  <InvalidBadge label="STALE" />
                )
              }
            >
              <p className="font-bold text-[13px] text-[#0b1c30]">
                {data.areas.historical_candles.status === 'ok'
                  ? 'Valid (Not Expired)'
                  : data.areas.historical_candles.message}
              </p>
            </StageMetricCard>
            <StageMetricCard label="COVERAGE">
              <p className="font-mono text-[12px] font-semibold text-[#0b1c30]">
                {data.areas.historical_candles.symbols_covered}/
                {data.areas.historical_candles.expected_count} symbols
              </p>
              <p className="text-[11px] text-[#45464d]">
                Latest: {data.areas.historical_candles.latest_date ?? '—'} · Expected:{' '}
                {data.areas.historical_candles.expected_prior_session ?? '—'}
              </p>
            </StageMetricCard>
            <StageMetricCard label="TARGET GENERATION">
              <p className="font-bold text-[13px] text-[#0b1c30]">
                {data.areas.historical_candles.status === 'ok' ? 'NOT REQUIRED' : 'REQUIRED'}
              </p>
            </StageMetricCard>
          </ChecklistStage>

          {/* Stage 04 */}
          <ChecklistStage
            stageNumber="STAGE 04"
            title="BASELINES"
            status={baselines.status}
            badgeLabel={baseBadge}
            expanded={open.baselines}
            onToggle={() => toggleStage('baselines')}
            secondaryAction={{
              label: 'Check Baselines',
              onClick: () => void onRefresh(),
            }}
            primaryAction={
              baselines.generate_action
                ? {
                    label: baselines.generate_action.label,
                    onClick: () => void handleGenerate('baselines'),
                    variant: baselines.status === 'ok' ? 'primary' : 'danger',
                    loading: generatingTask === 'baselines',
                  }
                : undefined
            }
          >
            <StageMetricCard
              label="BASELINE VECTOR STATUS"
              badge={baselines.status === 'ok' ? <ValidBadge /> : <InvalidBadge />}
              danger={baselines.status !== 'ok'}
            >
              <p
                className={`font-bold text-[13px] ${
                  baselines.status === 'ok' ? 'text-[#0b1c30]' : 'text-[#ba1a1a]'
                }`}
              >
                {baselines.status === 'ok' ? 'Valid' : 'Invalid (Missing / Stale)'}
              </p>
              <div className="w-full bg-[#ffdad6]/50 h-1.5 rounded mt-1 overflow-hidden">
                <div
                  className={`h-full ${baselines.status === 'ok' ? 'bg-[#006c4a]' : 'bg-[#ba1a1a]'}`}
                  style={{
                    width: `${Math.min(
                      100,
                      Math.round(
                        (baselines.symbols_covered / Math.max(1, baselines.expected_count)) * 100,
                      ),
                    )}%`,
                  }}
                />
              </div>
              <p className="text-[11px] text-[#45464d] mt-0.5">
                {baselines.symbols_covered}/{baselines.expected_count} READY · Reliable{' '}
                {baselines.reliable_count}
              </p>
            </StageMetricCard>
            <StageMetricCard label="DATA AS OF" danger={baselines.status !== 'ok'}>
              <p className="font-mono text-[12px] font-semibold text-[#0b1c30]">
                {baselines.baseline_as_of ?? '—'}
              </p>
              <p className="text-[11px] text-[#ba1a1a]">
                Expected: {baselines.expected_as_of ?? '—'}
              </p>
            </StageMetricCard>
            <StageMetricCard label="TARGET GENERATION" danger={baselines.status !== 'ok'}>
              <p className="font-bold text-[13px] text-[#ba1a1a]">
                {baselines.status === 'ok' ? 'NOT REQUIRED' : `${data.session_date} (Target)`}
              </p>
              <p className="text-[11px] text-[#ba1a1a]">
                {baselines.status === 'ok'
                  ? 'Partition current'
                  : 'Immediate Generation Required • Pipeline Halted'}
              </p>
            </StageMetricCard>
          </ChecklistStage>

          {/* Stage 05 */}
          <ChecklistStage
            stageNumber="STAGE 05"
            title="GENERATE 5 MIN CANDLES"
            status={data.areas.five_minute_candles.status}
            badgeLabel={fiveBadge}
            expanded={open.five_minute}
            onToggle={() => toggleStage('five_minute')}
            secondaryAction={{
              label: 'Check 5-Minute Candles',
              onClick: () => void onRefresh(),
            }}
            primaryAction={
              data.areas.five_minute_candles.generate_action
                ? {
                    label: data.areas.five_minute_candles.generate_action.label,
                    onClick: () => void handleGenerate('five-minute'),
                    variant: data.areas.five_minute_candles.status === 'ok' ? 'primary' : 'danger',
                    loading: generatingTask === 'five-minute',
                  }
                : undefined
            }
          >
            <StageMetricCard
              label="SEED STATUS"
              badge={
                data.areas.five_minute_candles.status === 'ok' ? (
                  <ValidBadge label="SEEDED" />
                ) : (
                  <InvalidBadge />
                )
              }
              danger={data.areas.five_minute_candles.status !== 'ok'}
            >
              <p className="font-bold text-[13px] text-[#0b1c30]">
                {data.areas.five_minute_candles.status === 'ok'
                  ? 'Valid'
                  : data.areas.five_minute_candles.message}
              </p>
            </StageMetricCard>
            <StageMetricCard label="COVERAGE">
              <p className="font-mono text-[12px] font-semibold text-[#0b1c30]">
                {data.areas.five_minute_candles.symbols_covered}/
                {data.areas.five_minute_candles.expected_count}
              </p>
              <p className="text-[11px] text-[#45464d]">
                EMA seed {data.areas.five_minute_candles.ema_seed_ready}/
                {data.areas.five_minute_candles.expected_count}
              </p>
            </StageMetricCard>
            <StageMetricCard label="LATEST / EXPECTED">
              <p className="font-mono text-[12px] font-semibold text-[#0b1c30]">
                {data.areas.five_minute_candles.latest_date ?? '—'}
              </p>
              <p className="text-[11px] text-[#45464d]">
                Expected: {data.areas.five_minute_candles.expected_prior_session ?? '—'}
              </p>
            </StageMetricCard>
          </ChecklistStage>
        </div>

        {/* Right column */}
        <aside className="xl:col-span-4 flex flex-col gap-3 xl:sticky xl:top-2">
          <section className="bg-white border border-[#e5e7eb] rounded-[4px] shadow-sm p-3">
            <h2 className="font-bold text-[14px] text-[#0b1c30] mb-3 tracking-tight">
              Pre-Market Timeline
            </h2>
            <ol className="space-y-2">
              {stageStatuses.map((s, idx) => {
                const ok = isOk(s.status)
                const bad = isBlocked(s.status)
                return (
                  <li key={s.id}>
                    <button
                      type="button"
                      onClick={() => focusStage(s.id)}
                      className="w-full flex items-center gap-2 px-1 py-1.5 text-left hover:bg-[#eff4ff] rounded-[2px]"
                    >
                      <span
                        className={`flex items-center justify-center size-5 rounded-full shrink-0 ${
                          ok
                            ? 'bg-[#82f5c1] text-[#006c4a]'
                            : bad
                              ? 'bg-[#ffdad6] text-[#ba1a1a]'
                              : 'bg-[#e5eeff] text-[#76777d]'
                        }`}
                      >
                        <span className="material-symbols-outlined text-[14px]">
                          {ok ? 'check' : bad ? 'priority_high' : 'close'}
                        </span>
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="font-mono text-[10px] text-[#76777d] leading-3">
                          STAGE {String(idx + 1).padStart(2, '0')}
                        </p>
                        <p className="font-semibold text-[12px] text-[#0b1c30] truncate leading-4">
                          {s.label}
                        </p>
                      </div>
                    </button>
                  </li>
                )
              })}
            </ol>
          </section>

          <section className="bg-[#0b1c30] text-slate-200 rounded-[4px] overflow-hidden shadow-sm border border-[#0b1c30]">
            <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-white/10">
              <div className="flex items-center gap-2">
                <span className="size-1.5 rounded-full bg-[#82f5c1] pulse-green" />
                <h2 className="font-mono text-[10px] font-bold tracking-[0.5px] uppercase text-slate-200">
                  Live CLI Diagnostic Stream
                </h2>
              </div>
              <label className="flex items-center gap-1.5 font-mono text-[10px] text-slate-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                  className="accent-[#82f5c1]"
                />
                Auto-refresh: 5s
              </label>
            </div>
            <div className="px-3 py-2 font-mono text-[10px] leading-relaxed max-h-72 overflow-y-auto space-y-1">
              {cliLines.map((line, i) => (
                <p
                  key={`${i}-${line.slice(0, 20)}`}
                  className={
                    line.includes(' ERR') || line.includes('[BLOCKER]')
                      ? 'text-[#ffdad6]'
                      : line.includes('[GENERATE')
                        ? 'text-[#ffe08c]'
                        : 'text-slate-300'
                  }
                >
                  {line}
                </p>
              ))}
            </div>
            <div className="px-3 py-2 border-t border-white/10 flex items-center gap-2">
              <code className="font-mono text-[10px] text-slate-400 truncate flex-1">
                READY&gt; {data.suggested_commands.runner}
              </code>
              <button
                type="button"
                onClick={() =>
                  void navigator.clipboard.writeText(cliLines.join('\n') || data.suggested_commands.runner)
                }
                className="bg-white/10 hover:bg-white/15 px-2 py-1 rounded-[2px] font-mono text-[10px] text-white shrink-0"
              >
                Copy CLI
              </button>
            </div>
          </section>
        </aside>
      </div>
    </div>
  )
}

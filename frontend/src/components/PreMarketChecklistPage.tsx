import { useCallback, useEffect, useMemo, useState } from 'react'
import { postGenerateLocalData } from '../api/client'
import type { CheckTokenResponse, ChecklistStatus, PreMarketChecklistResponse } from '../api/types'
import { formatDateTimeIst } from '../lib/format'
import {
  mergeKiteAuthStatus,
  computeEffectiveOverallStatus,
  effectiveNextStep,
} from '../hooks/usePreMarketChecklist'
import { StatusField } from './ui/StatusField'
import { ChecklistStage, shouldExpandByDefault } from './checklist/ChecklistStage'
import { CopyCommandButton } from './checklist/CopyCommandButton'

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

function formatCheckedAt(iso?: string): string {
  if (!iso) return '—'
  return formatDateTimeIst(iso)
}

function isComplete(status: ChecklistStatus): boolean {
  return status === 'ok'
}

function timelineIcon(status: ChecklistStatus): { icon: string; className: string } {
  if (status === 'ok') return { icon: 'check_circle', className: 'text-positive' }
  if (status === 'failed') return { icon: 'cancel', className: 'text-negative' }
  if (status === 'needs_update' || status === 'warning') {
    return { icon: 'error', className: 'text-warning' }
  }
  return { icon: 'radio_button_unchecked', className: 'text-on-surface-variant' }
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
  const [generateMessage, setGenerateMessage] = useState<string | null>(null)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [runningAll, setRunningAll] = useState(false)
  const [expanded, setExpanded] = useState<Record<StageId, boolean> | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [cliLines, setCliLines] = useState<string[]>([])

  const handleGenerate = useCallback(
    async (task: string) => {
      setGeneratingTask(task)
      setGenerateMessage(null)
      setGenerateError(null)
      try {
        const result = await postGenerateLocalData(task, data?.session_date)
        setGenerateMessage(result.message)
        setCliLines((prev) => [
          ...prev.slice(-40),
          `[${new Date().toLocaleTimeString('en-IN', { hour12: false, timeZone: 'Asia/Kolkata' })}] [GENERATE:${task}] ${result.message}`,
        ])
        await onRefresh()
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Generation failed'
        setGenerateError(msg)
        setCliLines((prev) => [
          ...prev.slice(-40),
          `[${new Date().toLocaleTimeString('en-IN', { hour12: false, timeZone: 'Asia/Kolkata' })}] [GENERATE:${task}] ERR ${msg}`,
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
      ...prev.slice(-40),
      `[${new Date().toLocaleTimeString('en-IN', { hour12: false, timeZone: 'Asia/Kolkata' })}] [KITE] ${result.message ?? (result.valid ? 'Token valid' : 'Token invalid')}`,
    ])
  }, [onCheckToken])

  // Seed expand state once; refresh CLI when checklist payload identity changes
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
    const stamp = new Date().toLocaleTimeString('en-IN', {
      hour12: false,
      timeZone: 'Asia/Kolkata',
    })
    const lines = [
      `[${stamp}] [CHECKLIST] session=${data.session_date} overall=${data.overall_status}`,
      ...data.blockers.slice(0, 6).map((b) => `[${stamp}] [BLOCKER] ${b}`),
      `[${stamp}] [NEXT] ${data.next_step}`,
      `[${stamp}] [RUNNER] ${data.suggested_commands.runner}`,
    ]
    setCliLines(lines)
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
      const base = prev ?? {
        kite: false,
        instruments: false,
        historical: false,
        baselines: false,
        five_minute: false,
      }
      return { ...base, [id]: !base[id] }
    })
  }, [])

  const focusStage = useCallback((id: StageId) => {
    setExpanded((prev) => {
      const base = prev ?? {
        kite: false,
        instruments: false,
        historical: false,
        baselines: false,
        five_minute: false,
      }
      return { ...base, [id]: true }
    })
  }, [])

  const handleRunAllPending = useCallback(async () => {
    if (!data) return
    setRunningAll(true)
    setGenerateError(null)
    setGenerateMessage(null)
    try {
      const kiteBase = data.areas.kite_auth
      const tokenValidatedFromApi = kiteBase.token_validated_today === true
      const kiteStatus = mergeKiteAuthStatus(
        kiteBase.status,
        tokenCheck !== null || tokenValidatedFromApi,
        tokenCheck?.valid ?? (tokenValidatedFromApi ? true : null),
      )
      if (kiteStatus !== 'ok') {
        await handleCheckToken()
      }
      const jobs: { task: string; status: ChecklistStatus; enabled?: boolean }[] = [
        {
          task: 'instruments',
          status: data.areas.instruments.status,
          enabled: Boolean(data.areas.instruments.generate_action),
        },
        {
          task: 'historical',
          status: data.areas.historical_candles.status,
          enabled: Boolean(data.areas.historical_candles.generate_action),
        },
        {
          task: 'baselines',
          status: data.areas.baselines.status,
          enabled: Boolean(data.areas.baselines.generate_action),
        },
        {
          task: 'five-minute',
          status: data.areas.five_minute_candles.status,
          enabled: Boolean(data.areas.five_minute_candles.generate_action),
        },
      ]
      for (const job of jobs) {
        if (job.enabled && job.status !== 'ok') {
          await handleGenerate(job.task)
        }
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
    const kiteValidLabel =
      tokenCheck === null && !tokenValidatedFromApi
        ? 'Not validated'
        : (tokenCheck?.valid ?? tokenValidatedFromApi)
          ? 'Valid'
          : 'Invalid'
    const kiteMessage = tokenCheck?.message ?? (kiteStatus !== 'ok' ? kiteBase.message : null)
    const stageStatuses = [
      { id: 'kite' as const, status: kiteStatus, label: 'KITE AUTH' },
      { id: 'instruments' as const, status: data.areas.instruments.status, label: 'INSTRUMENTS' },
      {
        id: 'historical' as const,
        status: data.areas.historical_candles.status,
        label: 'GENERATE 1 MINUTE CANDLE',
      },
      { id: 'baselines' as const, status: data.areas.baselines.status, label: 'BASELINES' },
      {
        id: 'five_minute' as const,
        status: data.areas.five_minute_candles.status,
        label: 'GENERATE 5 MIN CANDLES',
      },
    ] as const
    return { kiteBase, kiteStatus, kiteValidLabel, kiteMessage, stageStatuses }
  }, [data, tokenCheck])

  if (loading && !data) {
    return (
      <div className="flex-1 flex items-center justify-center text-on-surface-variant text-sm">
        Loading pre-market checklist…
      </div>
    )
  }

  if (!data || !derived) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-sm">
        {error && (
          <div className="px-3 py-2 bg-red-50 border border-red-200 text-red-800 max-w-lg">{error}</div>
        )}
        <button
          type="button"
          onClick={() => void onRefresh()}
          className="px-3 py-1.5 bg-primary text-white rounded label-caps text-[10px] font-bold"
        >
          Retry
        </button>
      </div>
    )
  }

  const { kiteBase, kiteStatus, kiteValidLabel, kiteMessage, stageStatuses } = derived

  const completedCount = stageStatuses.filter((s) => isComplete(s.status)).length
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

  const primaryBlocker =
    data.blockers[0] ??
    (data.areas.baselines.status !== 'ok' ? data.areas.baselines.message : null) ??
    (data.areas.offline_checks.status !== 'ok' ? data.areas.offline_checks.message : null) ??
    (data.areas.dashboard_readiness.status !== 'ok'
      ? data.areas.dashboard_readiness.message
      : null)

  const blockedStageLabel =
    stageStatuses.find((s) => s.status === 'failed' || s.status === 'needs_update')?.label ??
    'PIPELINE'

  return (
    <div className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-4">
      {error && (
        <div className="px-3 py-2 bg-red-50 border border-red-200 text-red-800 text-sm">{error}</div>
      )}
      {generateError && (
        <div className="px-3 py-2 bg-red-50 border border-red-200 text-red-800 text-sm whitespace-pre-wrap">
          {generateError}
        </div>
      )}
      {generateMessage && (
        <div className="px-3 py-2 bg-emerald-50 border border-emerald-200 text-positive text-sm whitespace-pre-wrap">
          {generateMessage}
        </div>
      )}

      {/* Pipeline gate header */}
      <section className="bg-white border border-outline-variant rounded-sm p-4 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-1">
              <h1 className="text-lg font-extrabold uppercase tracking-tight text-on-surface">
                Morning Pre-Market Checklist
              </h1>
              <span
                className={`label-caps px-2 py-1 border rounded-sm font-extrabold ${
                  gateLocked
                    ? 'bg-red-50 text-negative border-red-200'
                    : 'bg-emerald-50 text-positive border-emerald-200'
                }`}
              >
                {gateLocked ? 'GATE LOCKED (INCOMPLETE)' : 'GATE OPEN'}
              </span>
            </div>
            <p className="text-xs text-on-surface-variant max-w-3xl">
              Automated 5-stage verification before live observation. Offline checks and dashboard
              readiness feed the gate status below.
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => void handleRunAllPending()}
              disabled={runningAll || loading || overallReady}
              className="px-3 py-2 bg-on-surface text-white rounded-sm label-caps text-[10px] font-bold disabled:opacity-50 hover:bg-on-surface/90"
            >
              {runningAll ? 'Running…' : 'Run All Pending Checks'}
            </button>
            <button
              type="button"
              onClick={() => void onRefresh()}
              disabled={loading}
              className="w-9 h-9 flex items-center justify-center border border-outline-variant rounded-sm bg-white hover:bg-surface-container-low disabled:opacity-50"
              title="Refresh"
            >
              <span className="material-symbols-outlined text-[18px]">refresh</span>
            </button>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <p className="label-caps text-on-surface-variant">
              Pipeline verification: {completedCount} of {stageStatuses.length} stages complete (
              {progressPct}%)
            </p>
            <p className="label-caps text-on-surface-variant truncate max-w-[50%] text-right">
              {displayNextStep}
            </p>
          </div>
          <div className="flex h-2 rounded-sm overflow-hidden border border-outline-variant bg-surface-container">
            {stageStatuses.map((s) => (
              <div
                key={s.id}
                className={`flex-1 ${
                  s.status === 'ok'
                    ? 'bg-positive'
                    : s.status === 'failed'
                      ? 'bg-negative'
                      : s.status === 'needs_update' || s.status === 'warning'
                        ? 'bg-warning'
                        : 'bg-outline-variant/50'
                }`}
                title={`${s.label}: ${s.status}`}
              />
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <div className="border border-outline-variant rounded-sm p-3 bg-surface-container-low">
            <p className="label-caps text-on-surface-variant mb-1">Current Trading Day</p>
            <p className="text-sm font-extrabold font-data text-on-surface">{data.session_date}</p>
            <p className="text-[11px] text-on-surface-variant mt-1">Active NSE market session</p>
          </div>
          <div className="border border-outline-variant rounded-sm p-3 bg-surface-container-low">
            <p className="label-caps text-on-surface-variant mb-1">Universe Coverage</p>
            <p className="text-sm font-extrabold font-data text-on-surface">
              {instruments.instruments_count} / {instruments.expected_count}
            </p>
            <p className="text-[11px] text-positive mt-1">{universePct}% SYNC</p>
          </div>
          <div
            className={`border rounded-sm p-3 ${
              gateLocked
                ? 'border-red-200 bg-red-50'
                : 'border-emerald-200 bg-emerald-50'
            }`}
          >
            <p className="label-caps text-on-surface-variant mb-1">Pipeline Blocker</p>
            <p
              className={`text-sm font-extrabold ${
                gateLocked ? 'text-negative' : 'text-positive'
              }`}
            >
              {gateLocked ? blockedStageLabel : 'NONE'}
            </p>
            <p className={`text-[11px] mt-1 ${gateLocked ? 'text-negative' : 'text-positive'}`}>
              {gateLocked ? 'HALTED' : 'CLEAR'}
            </p>
          </div>
          <div
            className={`border rounded-sm p-3 ${
              data.areas.dashboard_readiness.market_hour_trial_ready
                ? 'border-emerald-200 bg-emerald-50'
                : 'border-red-200 bg-red-50'
            }`}
          >
            <p className="label-caps text-on-surface-variant mb-1">Start Observation</p>
            <p
              className={`text-sm font-extrabold ${
                data.areas.dashboard_readiness.market_hour_trial_ready
                  ? 'text-positive'
                  : 'text-negative'
              }`}
            >
              {data.areas.dashboard_readiness.market_hour_trial_ready ? 'READY' : 'NOT READY'}
            </p>
            <p className="text-[11px] text-on-surface-variant mt-1 line-clamp-2">
              {data.areas.dashboard_readiness.trial_ready_reason ||
                (overallReady ? 'All 5 stages clear' : 'All 5 stages required')}
            </p>
          </div>
        </div>

        {primaryBlocker && gateLocked && (
          <div className="flex flex-wrap items-center justify-between gap-3 px-3 py-3 border border-red-200 bg-red-50 rounded-sm">
            <div className="min-w-0">
              <p className="label-caps text-negative font-extrabold mb-1">
                Critical readiness blocker
              </p>
              <p className="text-xs text-negative">{primaryBlocker}</p>
              {(data.areas.offline_checks.status !== 'ok' ||
                data.areas.dashboard_readiness.status !== 'ok') && (
                <p className="text-[11px] text-on-surface-variant mt-1">
                  Offline: {data.areas.offline_checks.status} · Dashboard:{' '}
                  {data.areas.dashboard_readiness.status}
                </p>
              )}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {data.areas.baselines.generate_action && data.areas.baselines.status !== 'ok' && (
                <button
                  type="button"
                  onClick={() => void handleGenerate('baselines')}
                  disabled={generatingTask === 'baselines'}
                  className="px-3 py-1.5 bg-negative text-white rounded-sm label-caps text-[10px] font-bold disabled:opacity-50"
                >
                  {generatingTask === 'baselines' ? 'Generating…' : 'Execute: Generate Baselines Now'}
                </button>
              )}
              <button
                type="button"
                onClick={() => focusStage('baselines')}
                className="px-3 py-1.5 border border-red-200 bg-white text-negative rounded-sm label-caps text-[10px] font-bold"
              >
                View Run Log
              </button>
            </div>
          </div>
        )}
      </section>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_380px] gap-4 items-start">
        <div className="space-y-3 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <h2 className="label-caps text-on-surface tracking-wider">Execution Gate Milestones</h2>
            <span className="label-caps text-on-surface-variant">
              {completedCount}/{stageStatuses.length} complete
            </span>
          </div>

          <ChecklistStage
            stageNumber="STAGE 01"
            title="Kite Authentication"
            status={kiteStatus}
            badgeLabel={kiteStatus === 'ok' ? 'ACTIVE' : undefined}
            expanded={open.kite}
            onToggle={() => toggleStage('kite')}
            statusMessage={kiteMessage}
            primaryAction={{
              label: 'Check Auth Status',
              onClick: () => void handleCheckToken(),
              variant: 'primary',
              loading: tokenChecking,
            }}
            secondaryAction={{ label: 'Go to Settings', onClick: onGoToAuth }}
            copyCommand={kiteBase.copy_command}
            copyLabel="Copy Check Command"
          >
            <StatusField label="Broker status">{kiteValidLabel}</StatusField>
            <StatusField label="Token configured">
              {kiteBase.access_token_present ? 'Yes' : 'No'}
            </StatusField>
            {kiteBase.masked_access_token && (
              <StatusField label="Token preview">{kiteBase.masked_access_token}</StatusField>
            )}
            <StatusField label="Last checked">
              {tokenCheckedAt
                ? formatCheckedAt(tokenCheckedAt)
                : kiteBase.token_checked_at
                  ? formatCheckedAt(kiteBase.token_checked_at)
                  : '—'}
            </StatusField>
          </ChecklistStage>

          <ChecklistStage
            stageNumber="STAGE 02"
            title="Instruments Universe"
            status={data.areas.instruments.status}
            badgeLabel={data.areas.instruments.status === 'ok' ? 'SYNCED' : undefined}
            expanded={open.instruments}
            onToggle={() => toggleStage('instruments')}
            statusMessage={data.areas.instruments.message}
            primaryAction={{ label: 'Check Instruments', onClick: () => void onRefresh() }}
            generateActionLabel={data.areas.instruments.generate_action?.label}
            onGenerate={
              data.areas.instruments.generate_action
                ? () => void handleGenerate('instruments')
                : undefined
            }
            generating={generatingTask === 'instruments'}
            copyCommand={data.areas.instruments.copy_command}
          >
            <StatusField label="Instruments">
              {instruments.instruments_count}/{instruments.expected_count}
            </StatusField>
            <StatusField label="Tick-size coverage">
              {instruments.tick_size_count}/{instruments.expected_count}
            </StatusField>
            <StatusField label="Last updated">
              {formatDateTimeIst(instruments.last_updated)}
            </StatusField>
            {instruments.missing_symbols.length > 0 && (
              <StatusField label="Missing symbols">
                {instruments.missing_symbols.join(', ')}
              </StatusField>
            )}
          </ChecklistStage>

          <ChecklistStage
            stageNumber="STAGE 03"
            title="Historical / 1-Minute Candles"
            status={data.areas.historical_candles.status}
            badgeLabel={data.areas.historical_candles.status === 'ok' ? 'UP TO DATE' : undefined}
            expanded={open.historical}
            onToggle={() => toggleStage('historical')}
            statusMessage={data.areas.historical_candles.message}
            primaryAction={{ label: 'Check Candles', onClick: () => void onRefresh() }}
            generateActionLabel={data.areas.historical_candles.generate_action?.label}
            onGenerate={
              data.areas.historical_candles.generate_action
                ? () => void handleGenerate('historical')
                : undefined
            }
            generating={generatingTask === 'historical'}
            copyCommand={data.areas.historical_candles.copy_command}
            copyLabel="Copy Collector Command"
          >
            <StatusField label="Expected prior session">
              {data.areas.historical_candles.expected_prior_session ?? '—'}
            </StatusField>
            <StatusField label="Latest date">
              {data.areas.historical_candles.latest_date ?? '—'}
            </StatusField>
            <StatusField label="Symbols">
              {data.areas.historical_candles.symbols_covered}/
              {data.areas.historical_candles.expected_count}
            </StatusField>
            <StatusField label="Missing">{data.areas.historical_candles.missing_count}</StatusField>
          </ChecklistStage>

          <ChecklistStage
            stageNumber="STAGE 04"
            title="Baselines & Volatility Vectors"
            status={data.areas.baselines.status}
            badgeLabel={data.areas.baselines.status === 'ok' ? 'VALID' : undefined}
            expanded={open.baselines}
            onToggle={() => toggleStage('baselines')}
            statusMessage={data.areas.baselines.message}
            primaryAction={{ label: 'Check Baselines', onClick: () => void onRefresh() }}
            generateActionLabel={data.areas.baselines.generate_action?.label}
            onGenerate={
              data.areas.baselines.generate_action
                ? () => void handleGenerate('baselines')
                : undefined
            }
            generating={generatingTask === 'baselines'}
            copyCommand={data.areas.baselines.copy_command}
            copyLabel="Copy Generator Command"
          >
            <StatusField label="As-of">{data.areas.baselines.baseline_as_of ?? '—'}</StatusField>
            <StatusField label="Expected as-of">
              {data.areas.baselines.expected_as_of ?? '—'}
            </StatusField>
            <StatusField label="Symbols">
              {data.areas.baselines.symbols_covered}/{data.areas.baselines.expected_count}
            </StatusField>
            <StatusField label="Reliable">{data.areas.baselines.reliable_count}</StatusField>
          </ChecklistStage>

          <ChecklistStage
            stageNumber="STAGE 05"
            title="Intraday 5-Minute Seed Candles"
            status={data.areas.five_minute_candles.status}
            badgeLabel={data.areas.five_minute_candles.status === 'ok' ? 'SEEDED' : undefined}
            expanded={open.five_minute}
            onToggle={() => toggleStage('five_minute')}
            statusMessage={data.areas.five_minute_candles.message}
            primaryAction={{ label: 'Check 5-Minute Candles', onClick: () => void onRefresh() }}
            generateActionLabel={data.areas.five_minute_candles.generate_action?.label}
            onGenerate={
              data.areas.five_minute_candles.generate_action
                ? () => void handleGenerate('five-minute')
                : undefined
            }
            generating={generatingTask === 'five-minute'}
            copyCommand={data.areas.five_minute_candles.copy_command}
          >
            <StatusField label="Expected prior session">
              {data.areas.five_minute_candles.expected_prior_session ?? '—'}
            </StatusField>
            <StatusField label="Latest date">
              {data.areas.five_minute_candles.latest_date ?? '—'}
            </StatusField>
            <StatusField label="Symbols">
              {data.areas.five_minute_candles.symbols_covered}/
              {data.areas.five_minute_candles.expected_count}
            </StatusField>
            <StatusField label="EMA seed ready">
              {data.areas.five_minute_candles.ema_seed_ready}/
              {data.areas.five_minute_candles.expected_count}
            </StatusField>
          </ChecklistStage>
        </div>

        <aside className="space-y-3 xl:sticky xl:top-0">
          <section className="bg-white border border-outline-variant rounded-sm p-3">
            <h2 className="label-caps text-on-surface tracking-wider mb-3">Pre-Market Timeline</h2>
            <ol className="space-y-2">
              {stageStatuses.map((s, idx) => {
                const tip = timelineIcon(s.status)
                return (
                  <li key={s.id}>
                    <button
                      type="button"
                      onClick={() => focusStage(s.id)}
                      className="w-full flex items-center gap-2 px-2 py-2 rounded-sm border border-outline-variant/70 hover:bg-surface-container-low text-left"
                    >
                      <span className={`material-symbols-outlined text-[18px] ${tip.className}`}>
                        {tip.icon}
                      </span>
                      <span className="label-caps text-on-surface-variant shrink-0">
                        STAGE {idx + 1}
                      </span>
                      <span className="label-caps text-on-surface font-bold truncate flex-1">
                        {s.label}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ol>
          </section>

          <section className="bg-[#0f172a] text-slate-200 border border-[#1e293b] rounded-sm overflow-hidden">
            <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-[#1e293b]">
              <h2 className="label-caps tracking-wider text-slate-300">Live CLI Diagnostic Stream</h2>
              <div className="flex items-center gap-2 cli-copy-btn">
                <label className="flex items-center gap-1.5 label-caps text-slate-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoRefresh}
                    onChange={(e) => setAutoRefresh(e.target.checked)}
                    className="accent-primary"
                  />
                  Auto-refresh: 5s
                </label>
                <CopyCommandButton
                  command={cliLines.join('\n') || data.suggested_commands.runner}
                  label="Copy CLI"
                />
              </div>
            </div>
            <div className="px-3 py-2 font-data text-[10px] leading-relaxed max-h-64 overflow-y-auto custom-scrollbar space-y-1">
              {cliLines.length === 0 ? (
                <p className="text-slate-500">No diagnostic lines yet.</p>
              ) : (
                cliLines.map((line, i) => (
                  <p
                    key={`${i}-${line.slice(0, 24)}`}
                    className={
                      line.includes(' ERR') || line.includes('[BLOCKER]')
                        ? 'text-red-300'
                        : 'text-slate-300'
                    }
                  >
                    {line}
                  </p>
                ))
              )}
            </div>
            <div className="px-3 py-2 border-t border-[#1e293b] flex items-center gap-2 cli-copy-btn">
              <code className="font-data text-[10px] text-slate-400 truncate flex-1">
                {data.suggested_commands.runner}
              </code>
              <CopyCommandButton command={data.suggested_commands.runner} label="Copy" />
            </div>
          </section>
        </aside>
      </div>
    </div>
  )
}

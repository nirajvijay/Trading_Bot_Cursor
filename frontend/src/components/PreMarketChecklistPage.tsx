import { useCallback, useState } from 'react'
import { postGenerateLocalData } from '../api/client'
import type { CheckTokenResponse, PreMarketChecklistResponse } from '../api/types'
import { formatDateTimeIst } from '../lib/format'
import { mergeKiteAuthStatus, computeEffectiveOverallStatus, effectiveNextStep } from '../hooks/usePreMarketChecklist'
import { StatusField } from './ui/StatusField'
import { ChecklistCard } from './checklist/ChecklistCard'
import { ChecklistStatusPill } from './checklist/ChecklistStatusPill'
import { CopyCommandButton } from './checklist/CopyCommandButton'

interface Props {
  data: PreMarketChecklistResponse | null
  loading: boolean
  error: string | null
  onRefresh: () => void
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

  const handleGenerate = useCallback(
    async (task: string) => {
      setGeneratingTask(task)
      setGenerateMessage(null)
      setGenerateError(null)
      try {
        const result = await postGenerateLocalData(task, data?.session_date)
        setGenerateMessage(result.message)
        onRefresh()
      } catch (err) {
        setGenerateError(err instanceof Error ? err.message : 'Generation failed')
      } finally {
        setGeneratingTask(null)
      }
    },
    [onRefresh, data?.session_date],
  )

  const handleCheckToken = useCallback(async () => {
    await onCheckToken()
  }, [onCheckToken])

  if (loading && !data) {
    return (
      <div className="flex-1 flex items-center justify-center text-on-surface-variant text-sm">
        Loading pre-market checklist…
      </div>
    )
  }

  if (!data) {
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

  const effectiveOverall = computeEffectiveOverallStatus(data, kiteStatus)
  const overallReady = effectiveOverall === 'ok'
  const displayNextStep = effectiveNextStep(data, effectiveOverall)
  const startupCommands = data.suggested_commands.startup.join('\n')
  const kiteMessage =
    tokenCheck?.message ?? (kiteStatus !== 'ok' ? kiteBase.message : null)

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

      {/* Summary banner */}
      <div className="bg-white border border-outline-variant p-4">
        <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
          <div>
            <h1 className="text-base font-extrabold uppercase tracking-tight text-on-surface">
              Pre-Market Checklist
            </h1>
            <p className="text-xs text-on-surface-variant mt-1">
              System validation for NIFTY 100 live observation pipeline.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`label-caps font-extrabold px-2.5 py-1 border rounded-sm ${
                overallReady
                  ? 'bg-emerald-50 text-positive border-emerald-200'
                  : 'bg-amber-50 text-warning border-amber-200'
              }`}
            >
              {overallReady ? 'Ready for Market Observation' : 'Needs Attention'}
            </span>
            <ChecklistStatusPill status={effectiveOverall} message={!overallReady ? displayNextStep : undefined} />
          </div>
        </div>

        {data.blockers.length > 0 && (
          <div className="mb-3">
            <p className="label-caps text-on-surface-variant mb-1">Blockers</p>
            <ul className="text-xs text-negative space-y-0.5 list-disc list-inside">
              {data.blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="mb-3">
          <p className="label-caps text-on-surface-variant mb-1">Next step</p>
          <p className="text-xs text-on-surface">{displayNextStep}</p>
          {data.local_data_dir && (
            <p className="text-[11px] text-on-surface-variant mt-1 font-data">
              Local data: {data.local_data_dir}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-outline-variant">
          <span className="label-caps text-on-surface-variant">Runner CLI Command</span>
          <code className="font-data text-[10px] bg-surface-container-low px-2 py-1 border border-outline-variant flex-1 min-w-0 truncate">
            {data.suggested_commands.runner}
          </code>
          <CopyCommandButton command={data.suggested_commands.runner} />
          <button
            type="button"
            onClick={() => void onRefresh()}
            disabled={loading}
            className="px-2.5 py-1 rounded label-caps text-[10px] font-bold border border-outline-variant bg-white hover:bg-surface-container-low disabled:opacity-50"
          >
            {loading ? 'Refreshing…' : 'Refresh All'}
          </button>
        </div>
      </div>

      {/* 3-column card grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        <ChecklistCard
          icon="vpn_key"
          title="Kite Auth"
          status={kiteStatus}
          statusMessage={kiteMessage}
          primaryAction={{
            label: 'Check Token',
            onClick: () => void handleCheckToken(),
            variant: 'primary',
            loading: tokenChecking,
          }}
          secondaryAction={{ label: 'Go to Kite Auth', onClick: onGoToAuth }}
          copyCommand={kiteBase.copy_command}
          copyLabel="Copy Check Command"
        >
          <StatusField label="Status">{kiteValidLabel}</StatusField>
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
          {tokenCheck?.message && (
            <p className="text-[11px] text-on-surface-variant mt-2">{tokenCheck.message}</p>
          )}
        </ChecklistCard>

        <ChecklistCard
          icon="list_alt"
          title="Instruments"
          status={data.areas.instruments.status}
          statusMessage={data.areas.instruments.message}
          primaryAction={{ label: 'Check Instruments', onClick: () => void onRefresh() }}
          generateAction={data.areas.instruments.generate_action}
          onGenerate={() => void handleGenerate('instruments')}
          generating={generatingTask === 'instruments'}
          copyCommand={data.areas.instruments.copy_command}
        >
          <StatusField label="Instruments">
            {data.areas.instruments.instruments_count}/{data.areas.instruments.expected_count}
          </StatusField>
          <StatusField label="Tick-size coverage">
            {data.areas.instruments.tick_size_count}/{data.areas.instruments.expected_count}
          </StatusField>
          <StatusField label="Last updated">
            {formatDateTimeIst(data.areas.instruments.last_updated)}
          </StatusField>
          {data.areas.instruments.missing_symbols.length > 0 && (
            <StatusField label="Missing symbols">
              {data.areas.instruments.missing_symbols.join(', ')}
            </StatusField>
          )}
        </ChecklistCard>

        <ChecklistCard
          icon="history"
          title="Historical Candles"
          status={data.areas.historical_candles.status}
          statusMessage={data.areas.historical_candles.message}
          primaryAction={{ label: 'Check Candles', onClick: () => void onRefresh() }}
          generateAction={data.areas.historical_candles.generate_action}
          onGenerate={() => void handleGenerate('historical')}
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
          <StatusField label="Missing">
            {data.areas.historical_candles.missing_count}
          </StatusField>
        </ChecklistCard>

        <ChecklistCard
          icon="analytics"
          title="Baselines"
          status={data.areas.baselines.status}
          statusMessage={data.areas.baselines.message}
          primaryAction={{ label: 'Check Baselines', onClick: () => void onRefresh() }}
          generateAction={data.areas.baselines.generate_action}
          onGenerate={() => void handleGenerate('baselines')}
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
        </ChecklistCard>

        <ChecklistCard
          icon="candlestick_chart"
          title="5-Minute Candles"
          status={data.areas.five_minute_candles.status}
          statusMessage={data.areas.five_minute_candles.message}
          primaryAction={{ label: 'Check 5-Minute Candles', onClick: () => void onRefresh() }}
          generateAction={data.areas.five_minute_candles.generate_action}
          onGenerate={() => void handleGenerate('five-minute')}
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
        </ChecklistCard>

        <ChecklistCard
          icon="health_and_safety"
          title="Offline Checks"
          status={data.areas.offline_checks.status}
          statusMessage={data.areas.offline_checks.message}
          primaryAction={{ label: 'Check Offline Status', onClick: () => void onRefresh() }}
          copyCommand={data.areas.offline_checks.copy_command}
          copyLabel="Copy Validation Command"
        >
          <StatusField label="API Health">
            {data.areas.offline_checks.api_health.toUpperCase()}
          </StatusField>
          <StatusField label="Database">
            {data.areas.offline_checks.database_readable ? 'OK' : 'Issue'}
          </StatusField>
          <StatusField label="Radar rows">{data.areas.offline_checks.radar_row_count}</StatusField>
          {(data.areas.offline_checks.databases ?? []).map((db) => (
            <StatusField key={db.name} label={db.name}>
              {db.exists ? (db.readable ? 'OK' : 'Unreadable') : 'Missing'}
              {db.scope === 'local' ? ' (local)' : ''}
            </StatusField>
          ))}
        </ChecklistCard>

        <ChecklistCard
          icon="dashboard"
          title="Dashboard Readiness"
          status={data.areas.dashboard_readiness.status}
          statusMessage={data.areas.dashboard_readiness.message}
          primaryAction={{ label: 'Check Dashboard', onClick: () => void onRefresh() }}
          copyCommand={startupCommands}
          copyLabel="Copy Startup Commands"
        >
          <StatusField label="API">
            {data.areas.dashboard_readiness.api_reachable ? 'OK' : 'Down'}
          </StatusField>
          <StatusField label="Frontend">OK</StatusField>
          <StatusField label="Latest session">
            {data.areas.dashboard_readiness.latest_session ?? '—'}
          </StatusField>
          <StatusField label="Market-hour trial">
            {data.areas.dashboard_readiness.market_hour_trial_ready ? 'Ready' : 'Not ready'}
          </StatusField>
          <p className="text-[11px] text-on-surface-variant mt-2">
            {data.areas.dashboard_readiness.trial_ready_reason}
          </p>
        </ChecklistCard>
      </div>
    </div>
  )
}

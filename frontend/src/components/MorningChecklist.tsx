import { useEffect, useState } from 'react'
import { postGenerateLocalData } from '../api/client'
import type {
  CheckTokenResponse,
  ChecklistStatus,
  PreMarketChecklistResponse,
} from '../api/types'
import { KiteAuthPage } from './KiteAuthPage'

type AreaKey =
  | 'instruments'
  | 'historical_candles'
  | 'baselines'
  | 'five_minute_candles'
  | 'offline_checks'
  | 'dashboard_readiness'

const STEPS: ReadonlyArray<{
  key: AreaKey
  title: string
  action: string | null
}> = [
  { key: 'instruments', title: 'Universe', action: 'instruments' },
  { key: 'historical_candles', title: '1m candles', action: 'historical' },
  { key: 'baselines', title: 'Baselines', action: 'baselines' },
  { key: 'five_minute_candles', title: '5m candles', action: 'five-minute' },
  { key: 'offline_checks', title: 'Pipeline', action: null },
  { key: 'dashboard_readiness', title: 'Observation ready', action: null },
]

function statusLabel(status: ChecklistStatus | undefined): string {
  switch (status) {
    case 'ok':
      return 'OK'
    case 'needs_update':
      return 'Update'
    case 'warning':
      return 'Warn'
    case 'failed':
      return 'Fail'
    case 'not_checked':
      return '—'
    default:
      return '—'
  }
}

function stepDetail(
  key: AreaKey,
  data: PreMarketChecklistResponse | null,
): string {
  if (!data) return 'Run refresh to load status.'
  const { areas } = data
  switch (key) {
    case 'instruments':
      return `${areas.instruments.instruments_count}/${areas.instruments.expected_count} symbols`
    case 'historical_candles': {
      const h = areas.historical_candles
      const complete =
        h.prior_session_complete == null
          ? 'complete ?'
          : h.prior_session_complete
            ? 'complete yes'
            : 'complete no'
      const bars =
        h.bars_on_prior_min == null
          ? ''
          : ` · bars ${h.bars_on_prior_min}/${h.bars_on_prior_avg ?? '—'}/${h.bars_on_prior_max ?? '—'}`
      return `${h.symbols_covered}/${h.expected_count} · prior ${h.expected_prior_session ?? '—'} · ${complete}${bars}`
    }
    case 'baselines':
      return `${areas.baselines.symbols_covered}/${areas.baselines.expected_count} · as-of ${areas.baselines.baseline_as_of ?? '—'}`
    case 'five_minute_candles':
      return `${areas.five_minute_candles.symbols_covered}/${areas.five_minute_candles.expected_count} · EMA ${areas.five_minute_candles.ema_seed_ready}/${areas.five_minute_candles.expected_count}`
    case 'offline_checks':
      return `API ${areas.offline_checks.api_health} · radar ${areas.offline_checks.radar_row_count}`
    case 'dashboard_readiness':
      return areas.dashboard_readiness.market_hour_trial_ready
        ? 'Ready for market hours'
        : 'Not ready'
  }
}

export function MorningChecklist({
  data,
  loading,
  error,
  onRefresh,
}: {
  data: PreMarketChecklistResponse | null
  loading: boolean
  error: string | null
  onRefresh: () => void
  tokenCheck: CheckTokenResponse | null
  tokenCheckedAt: string | null
  tokenChecking: boolean
  onCheckToken: () => Promise<CheckTokenResponse>
}) {
  const [task, setTask] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (!task && !loading) return
    const start = Date.now()
    setElapsed(0)
    const timer = setInterval(
      () => setElapsed(Math.floor((Date.now() - start) / 1000)),
      1000,
    )
    return () => clearInterval(timer)
  }, [task, loading])

  async function generate(action: string) {
    setTask(action)
    setMessage(null)
    setFailure(null)
    try {
      const result = await postGenerateLocalData(action, data?.session_date)
      setMessage(result.message)
      onRefresh()
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e))
    } finally {
      setTask(null)
    }
  }

  const kiteOk = data?.areas.kite_auth.status === 'ok'
  const allOk =
    !!data &&
    kiteOk &&
    STEPS.every(({ key }) => data.areas[key].status === 'ok')
  const nextHint = allOk
    ? 'All checks passed.'
    : (data?.next_step ?? 'Connect Kite, then refresh checks.')

  return (
    <div className="morning-checklist">
      <header className="mc-header">
        <div>
          <h1>Checklist</h1>
          <p className={allOk ? 'mc-summary is-ready' : 'mc-summary'}>{nextHint}</p>
        </div>
        <div className="mc-header-actions">
          <span className={allOk ? 'mc-badge is-ready' : 'mc-badge'}>
            {allOk ? 'Ready' : 'Needs attention'}
          </span>
          <button
            type="button"
            className="mc-btn"
            disabled={loading || !!task}
            onClick={onRefresh}
          >
            {loading ? `Checking… ${elapsed}s` : 'Refresh'}
          </button>
        </div>
      </header>

      {(error || failure) && (
        <p role="alert" className="mc-notice is-error">
          {error || failure}
        </p>
      )}
      {message && (
        <p role="status" className="mc-notice">
          {message}
        </p>
      )}
      {task && (
        <p role="status" className="mc-notice">
          Preparing {task} · {elapsed}s
        </p>
      )}

      <ol className="mc-steps">
        <li className={`mc-step ${kiteOk ? 'is-ok' : 'is-open'}`}>
          <div className="mc-step-head">
            <span className="mc-num">1</span>
            <h2>Kite</h2>
            <strong className={kiteOk ? 'mc-status is-ok' : 'mc-status'}>
              {statusLabel(data?.areas.kite_auth.status)}
            </strong>
          </div>
          <div className="mc-step-body">
            <KiteAuthPage embedded onTokenChecked={onRefresh} />
          </div>
        </li>

        {STEPS.map(({ key, title, action }, index) => {
          const area = data?.areas[key]
          const ok = area?.status === 'ok'
          const previousReady =
            !!data &&
            kiteOk &&
            STEPS.slice(0, index).every(
              ({ key: previous }) => data.areas[previous].status === 'ok',
            )
          const showAction = Boolean(action) && !ok
          const showIssue = Boolean(area && area.status !== 'ok' && area.message)

          return (
            <li key={key} className={`mc-step ${ok ? 'is-ok' : 'is-open'}`}>
              <div className="mc-step-head">
                <span className="mc-num">{index + 2}</span>
                <h2>{title}</h2>
                <strong className={ok ? 'mc-status is-ok' : 'mc-status'}>
                  {statusLabel(area?.status)}
                </strong>
              </div>
              <div className="mc-step-body">
                <p className="mc-detail">{stepDetail(key, data)}</p>
                {showIssue && <p className="mc-issue">{area?.message}</p>}
                {showAction && (
                  <div className="mc-step-actions">
                    <button
                      type="button"
                      className="mc-btn is-primary"
                      disabled={!!task || !previousReady}
                      onClick={() => void generate(action!)}
                    >
                      Prepare
                    </button>
                    {!previousReady && (
                      <span className="mc-hint">Finish earlier steps first</span>
                    )}
                  </div>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

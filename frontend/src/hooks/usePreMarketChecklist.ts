import { useCallback, useEffect, useState } from 'react'
import { fetchPreMarketChecklist } from '../api/client'
import type { ChecklistStatus, PreMarketChecklistResponse } from '../api/types'

const STATUS_RANK: Record<ChecklistStatus, number> = {
  not_checked: 0,
  ok: 1,
  warning: 2,
  needs_update: 3,
  failed: 4,
}

function worstStatus(...statuses: ChecklistStatus[]): ChecklistStatus {
  return statuses.reduce((worst, s) =>
    STATUS_RANK[s] > STATUS_RANK[worst] ? s : worst,
  )
}

export function usePreMarketChecklist(sessionDate: string, enabled: boolean) {
  const [data, setData] = useState<PreMarketChecklistResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const result = await fetchPreMarketChecklist(sessionDate)
      setData(result)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load checklist')
    } finally {
      setLoading(false)
    }
  }, [sessionDate])

  useEffect(() => {
    if (!enabled) return
    void refresh()
  }, [enabled, refresh])

  return { data, loading, error, refresh }
}

export function mergeKiteAuthStatus(
  baseStatus: ChecklistStatus,
  tokenChecked: boolean,
  tokenValid: boolean | null,
): ChecklistStatus {
  if (!tokenChecked) {
    if (baseStatus === 'failed') return 'failed'
    if (baseStatus === 'ok') return 'ok'
    return 'warning'
  }
  if (tokenValid === true) return 'ok'
  if (tokenValid === false) return 'failed'
  return baseStatus
}

export function computeEffectiveOverallStatus(
  data: PreMarketChecklistResponse,
  kiteStatus: ChecklistStatus,
): ChecklistStatus {
  const areas = data.areas
  return worstStatus(
    kiteStatus,
    areas.instruments.status,
    areas.historical_candles.status,
    areas.baselines.status,
    areas.five_minute_candles.status,
    areas.offline_checks.status,
    areas.dashboard_readiness.status,
  )
}

export function effectiveNextStep(
  data: PreMarketChecklistResponse,
  overallStatus: ChecklistStatus,
): string {
  if (overallStatus === 'ok') {
    return 'Start live observation runner during market hours'
  }
  return data.next_step
}

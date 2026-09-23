import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchChecklistActivity, fetchPreMarketChecklist } from '../api/client'
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

export function usePreMarketChecklist(sessionDate: string, enabled: boolean, visible = true) {
  const [data, setData] = useState<PreMarketChecklistResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const revision = useRef<string | undefined>(undefined)
  const requestId = useRef(0)

  const refresh = useCallback(async () => {
    const id = ++requestId.current
    setLoading(true)
    try {
      const result = await fetchPreMarketChecklist(sessionDate)
      if (id !== requestId.current) return
      revision.current = result.activity?.revision
      setData(result)
      setError(null)
    } catch (err) {
      if (id !== requestId.current) return
      setError(err instanceof Error ? err.message : 'Failed to load checklist')
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }, [sessionDate])

  useEffect(() => {
    if (!enabled) return
    void refresh()
  }, [enabled, refresh])

  useEffect(() => {
    if (!enabled || !visible) return
    let cancelled = false
    let polling = false
    const poll = async () => {
      if (document.hidden || polling) return
      polling = true
      try {
        const result = await fetchChecklistActivity(sessionDate)
        if (!cancelled && result.activity?.revision !== revision.current) await refresh()
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to refresh checklist')
      } finally {
        polling = false
      }
    }
    const onVisible = () => { if (!document.hidden) void refresh() }
    onVisible()
    const timer = window.setInterval(() => void poll(), 5000)
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [enabled, visible, sessionDate, refresh])

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
  if (data.activity?.status === 'running') return 'warning'
  if (data.activity?.status === 'blocked' || data.activity?.dirty?.length) return 'failed'
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

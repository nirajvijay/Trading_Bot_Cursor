import { useCallback, useEffect, useState } from 'react'
import { fetchPerformance } from '../api/client'
import type { PerformanceSummary } from '../api/types'

const POLL_MS = 30000

/**
 * The Performance tab's data: ARB's forward test for the stage being traded.
 * It changes when a trade closes or a daily check is stored, so a slow poll
 * is enough. `parityDate` adds that day's mismatched signals.
 */
export function usePerformance(parityDate: string | null) {
  const [data, setData] = useState<PerformanceSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setData(await fetchPerformance(parityDate ?? undefined))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load performance')
    } finally {
      setLoading(false)
    }
  }, [parityDate])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh()
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  return { data, loading, error, refresh }
}

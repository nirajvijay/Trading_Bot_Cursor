import { useCallback, useEffect, useState } from 'react'
import { fetchObservationReadiness } from '../api/client'
import type { ObservationReadiness } from '../api/types'

/** Slow poll — readiness is cache-backed; live badge uses /status. */
const DEFAULT_INTERVAL_MS = 60_000

export function useObservationReadiness(sessionDate: string, enabled: boolean) {
  const [readiness, setReadiness] = useState<ObservationReadiness | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const result = await fetchObservationReadiness(sessionDate)
      setReadiness(result)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load observation readiness')
    } finally {
      setLoading(false)
    }
  }, [sessionDate])

  useEffect(() => {
    if (!enabled) return
    void refresh()
  }, [enabled, refresh])

  useEffect(() => {
    if (!enabled) return
    const id = window.setInterval(() => {
      if (document.hidden) return
      void refresh()
    }, DEFAULT_INTERVAL_MS)
    return () => window.clearInterval(id)
  }, [enabled, refresh])

  return { readiness, loading, error, refresh }
}

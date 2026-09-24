import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchTradeCharges } from '../api/client'
import type { TradeChargesDay } from '../api/types'

/**
 * The Charges tab's data. Loaded on open, on a date change and on Refresh
 * only: charges change only when a trade closes, and each load for today
 * may ask Kite to price the trades not yet saved.
 */
export function useTradeCharges(sessionDate: string) {
  const [data, setData] = useState<TradeChargesDay | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Drop a slow response for a date the user has already moved away from.
  const latest = useRef(sessionDate)
  latest.current = sessionDate

  const refresh = useCallback(async () => {
    const asked = sessionDate
    setLoading(true)
    try {
      const day = await fetchTradeCharges(asked)
      if (latest.current !== asked) return
      setData(day)
      setError(null)
    } catch (err) {
      if (latest.current !== asked) return
      setError(err instanceof Error ? err.message : 'Failed to load charges')
    } finally {
      if (latest.current === asked) setLoading(false)
    }
  }, [sessionDate])

  useEffect(() => {
    setData(null)
    void refresh()
  }, [refresh])

  return { data, loading, error, refresh }
}

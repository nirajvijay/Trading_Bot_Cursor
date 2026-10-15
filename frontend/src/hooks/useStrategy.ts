import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchStrategyBreadth, fetchStrategyFunnel, fetchStrategyOrb } from '../api/client'
import type { FunnelFeed, MarketBreadth, OrbDay } from '../api/types'

const POLL_MS = 5000

/**
 * The Strategy tab's data: the ORB board and the funnel feed.
 *
 * Polled while the tab is open and showing today, since arms change state as
 * breakouts fire. A past date is loaded once.
 */
export function useStrategy(sessionDate: string, live: boolean) {
  const [orb, setOrb] = useState<OrbDay | null>(null)
  const [funnel, setFunnel] = useState<FunnelFeed | null>(null)
  const [breadth, setBreadth] = useState<MarketBreadth | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Drop a slow response for a date the user has already moved away from.
  const latest = useRef(sessionDate)
  latest.current = sessionDate

  const refresh = useCallback(async () => {
    const asked = sessionDate
    setLoading(true)
    try {
      const [orbDay, feed, market] = await Promise.all([
        fetchStrategyOrb(asked),
        fetchStrategyFunnel(asked),
        fetchStrategyBreadth(asked),
      ])
      if (latest.current !== asked) return
      setOrb(orbDay)
      setFunnel(feed)
      setBreadth(market)
      setError(null)
    } catch (err) {
      if (latest.current !== asked) return
      setError(err instanceof Error ? err.message : 'Failed to load strategy signals')
    } finally {
      if (latest.current === asked) setLoading(false)
    }
  }, [sessionDate])

  useEffect(() => {
    setOrb(null)
    setFunnel(null)
    setBreadth(null)
    void refresh()
    if (!live) return
    const timer = window.setInterval(() => void refresh(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh, live])

  return { orb, funnel, breadth, loading, error, refresh }
}

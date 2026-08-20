import { useCallback, useEffect, useState } from 'react'
import {
  fetchTradingEngineSnapshot,
  fetchTradingEngineStatus,
  postStartTradingEngine,
  postStopTradingEngine,
  postTradingCapital,
  postTrailStop,
} from '../api/client'
import type { TradingEngineSnapshot, TradingEngineStatus } from '../api/types'

const POLL_MS = 2000

export function useTradingEngine(sessionDate: string, enabled: boolean) {
  const [status, setStatus] = useState<TradingEngineStatus | null>(null)
  const [snapshot, setSnapshot] = useState<TradingEngineSnapshot | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [st, snap] = await Promise.all([
        fetchTradingEngineStatus(sessionDate),
        fetchTradingEngineSnapshot(sessionDate),
      ])
      setStatus(st)
      setSnapshot(snap)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load trading engine')
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
    }, POLL_MS)
    return () => window.clearInterval(id)
  }, [enabled, refresh])

  const start = useCallback(
    async (confirmLiveOrders: boolean, totalCapital: number) => {
      setStarting(true)
      setError(null)
      try {
        await postStartTradingEngine({
          confirm_live_orders: confirmLiveOrders,
          session_date: sessionDate,
          total_capital: totalCapital,
        })
        await refresh()
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to start trading engine')
        throw err
      } finally {
        setStarting(false)
      }
    },
    [refresh, sessionDate],
  )

  const stop = useCallback(async () => {
    setStopping(true)
    setError(null)
    try {
      await postStopTradingEngine()
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop trading engine')
    } finally {
      setStopping(false)
    }
  }, [refresh])

  const setCapital = useCallback(
    async (totalCapital: number) => {
      await postTradingCapital(totalCapital)
      await refresh()
    },
    [refresh],
  )

  const trailStop = useCallback(
    async (tradeId: string, newStop: number) => {
      await postTrailStop(tradeId, newStop)
      await refresh()
    },
    [refresh],
  )

  return {
    status,
    snapshot,
    loading,
    error,
    starting,
    stopping,
    refresh,
    start,
    stop,
    setCapital,
    trailStop,
  }
}

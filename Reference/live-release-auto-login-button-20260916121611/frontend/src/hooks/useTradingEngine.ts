import { useCallback, useEffect, useState } from 'react'
import {
  fetchTradingEngineSnapshot,
  fetchTradingEngineStatus,
  postAutoTrail,
  postStartTradingEngine,
  postStopTradingEngine,
  postTradingCapital,
  postTrailStop,
} from '../api/client'
import type { TradingEngineSnapshot, TradingEngineStatus } from '../api/types'

const POLL_MS = 2000
const TRANSITION_POLL_MS = 1000

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
    const ms = starting || stopping ? TRANSITION_POLL_MS : POLL_MS
    const id = window.setInterval(() => {
      if (document.hidden) return
      void refresh()
    }, ms)
    return () => window.clearInterval(id)
  }, [enabled, refresh, starting, stopping])

  useEffect(() => {
    if (!starting || !status) return
    if (status.engine_running && status.state !== 'starting') {
      setStarting(false)
    }
  }, [starting, status])

  useEffect(() => {
    if (!stopping || !status) return
    if (!status.engine_running) {
      setStopping(false)
    }
  }, [stopping, status])

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
        setStarting(false)
        setError(err instanceof Error ? err.message : 'Failed to start trading engine')
        throw err
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
      setStopping(false)
      setError(err instanceof Error ? err.message : 'Failed to stop trading engine')
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

  const autoTrail = useCallback(
    async (tradeId: string, enabled: boolean) => {
      await postAutoTrail(tradeId, enabled)
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
    autoTrail,
  }
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchCoverage, fetchRadar, fetchSessions, fetchStatus } from '../api/client'
import type { RadarRow, RunnerStatus, SessionCoverage } from '../api/types'

const DEFAULT_INTERVAL_MS = 5000

interface DashboardState {
  rows: RadarRow[]
  coverage: SessionCoverage | null
  status: RunnerStatus | null
  sessions: string[]
  loading: boolean
  error: string | null
}

export function useRadarDashboard(sessionDate: string, intervalMs = DEFAULT_INTERVAL_MS, enabled = true) {
  const [state, setState] = useState<DashboardState>({
    rows: [],
    coverage: null,
    status: null,
    sessions: [],
    loading: true,
    error: null,
  })
  const sessionRef = useRef(sessionDate)
  sessionRef.current = sessionDate

  const refresh = useCallback(async () => {
    const date = sessionRef.current
    try {
      const [radar, coverage, status, sessions] = await Promise.all([
        fetchRadar(date),
        fetchCoverage(date),
        fetchStatus(date),
        fetchSessions(),
      ])
      setState({
        rows: radar.rows,
        coverage,
        status,
        sessions,
        loading: false,
        error: null,
      })
    } catch (err) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: err instanceof Error ? err.message : 'Failed to load dashboard',
      }))
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    setState((prev) => ({ ...prev, loading: true, error: null }))
    void refresh()
  }, [sessionDate, refresh, enabled])

  useEffect(() => {
    if (!enabled) return
    const id = window.setInterval(() => {
      if (document.hidden) return
      void refresh()
    }, intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs, refresh, enabled])

  return { ...state, refresh }
}

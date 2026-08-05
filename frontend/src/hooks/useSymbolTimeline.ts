import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchSymbolTimeline } from '../api/client'
import type { SymbolTimelineResponse } from '../api/types'

const cache = new Map<string, SymbolTimelineResponse>()

function cacheKey(sessionDate: string, symbol: string): string {
  return `${sessionDate}:${symbol}`
}

interface TimelineState {
  data: SymbolTimelineResponse | null
  loading: boolean
  error: string | null
}

export function useSymbolTimeline(
  sessionDate: string,
  symbol: string | null,
  refreshToken = 0,
) {
  const [state, setState] = useState<TimelineState>({
    data: null,
    loading: false,
    error: null,
  })
  const sessionRef = useRef(sessionDate)
  sessionRef.current = sessionDate

  const refresh = useCallback(async () => {
    if (!symbol) return
    const date = sessionRef.current
    const key = cacheKey(date, symbol)
    const cached = cache.get(key)
    if (cached) {
      setState({ data: cached, loading: false, error: null })
    } else {
      setState((prev) => ({ ...prev, loading: true, error: null }))
    }

    try {
      const data = await fetchSymbolTimeline(date, symbol)
      cache.set(key, data)
      setState({ data, loading: false, error: null })
    } catch (err) {
      setState({
        data: cached ?? null,
        loading: false,
        error: err instanceof Error ? err.message : 'Failed to load timeline',
      })
    }
  }, [symbol])

  useEffect(() => {
    if (!symbol) {
      setState({ data: null, loading: false, error: null })
      return
    }
    void refresh()
  }, [symbol, sessionDate, refreshToken, refresh])

  return { ...state, refresh }
}

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchExecutionCommand,
  fetchExecutionPositions,
  fetchExecutionPreflight,
  fetchExecutionStatus,
  postExecutionCommand,
  postExecutionStart,
} from '../api/client'
import type {
  ExecutionCommand,
  ExecutionCommandKind,
  ExecutionPositions,
  ExecutionPreflight,
  ExecutionSessionCaps,
  ExecutionStatus,
} from '../api/types'

// Matches the engine's own 1s tick while it is live, so the desk is never
// more than about a second behind the loop it is watching.
const RUNNING_POLL_MS = 1000
// Nothing is changing when it is stopped; no reason to hammer the API.
const IDLE_POLL_MS = 5000

export const DEFAULT_CAPS: ExecutionSessionCaps = {
  per_trade_cap_rupees: 900,
  per_trade_cap_vwap_limited_rupees: 450,
  daily_loss_cap_rupees: 3000,
  total_capital_rupees: 300000,
  leverage_factor: 5,
}

export function useExecutionEngine(sessionDate: string, enabled: boolean) {
  const [status, setStatus] = useState<ExecutionStatus | null>(null)
  const [positions, setPositions] = useState<ExecutionPositions | null>(null)
  const [preflight, setPreflight] = useState<ExecutionPreflight | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const running = status?.engine_state === 'running'
  const runningRef = useRef(running)
  runningRef.current = running

  const refresh = useCallback(async () => {
    try {
      const [st, pos] = await Promise.all([
        fetchExecutionStatus(),
        fetchExecutionPositions(sessionDate),
      ])
      setStatus(st)
      setPositions(pos)
      setError(null)
      // Preconditions only matter while stopped, and asking for them every
      // second while running would poll the observation runner for nothing.
      if (st.engine_state !== 'running') {
        setPreflight(await fetchExecutionPreflight())
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load execution engine')
    }
  }, [sessionDate])

  useEffect(() => {
    if (!enabled) return
    void refresh()
  }, [enabled, refresh])

  useEffect(() => {
    if (!enabled) return
    const id = window.setInterval(
      () => {
        if (document.hidden) return
        void refresh()
      },
      running ? RUNNING_POLL_MS : IDLE_POLL_MS,
    )
    return () => window.clearInterval(id)
  }, [enabled, refresh, running])

  /** Poll a queued command until the engine resolves it, so an optimistic
   *  click is never left looking applied when it was actually rejected. */
  const settleCommand = useCallback(async (command: ExecutionCommand) => {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      if (command.status !== 'pending') break
      await new Promise((resolve) => window.setTimeout(resolve, 400))
      try {
        command = await fetchExecutionCommand(command.command_id)
      } catch {
        break
      }
    }
    if (command.status === 'rejected') {
      const reason =
        (command.result && (command.result as { reason?: string }).reason) || 'rejected'
      setActionError(`${command.kind} refused: ${reason}`)
    }
    await refresh()
    return command
  }, [refresh])

  const sendCommand = useCallback(
    async (kind: ExecutionCommandKind, tradeId?: string) => {
      setBusy(tradeId ? `${kind}:${tradeId}` : kind)
      setActionError(null)
      try {
        const command = await postExecutionCommand(kind, tradeId)
        return await settleCommand(command)
      } catch (err) {
        setActionError(err instanceof Error ? err.message : `${kind} failed`)
        return null
      } finally {
        setBusy(null)
      }
    },
    [settleCommand],
  )

  const start = useCallback(
    async (caps: ExecutionSessionCaps, liveOrders: boolean) => {
      setBusy('start')
      setActionError(null)
      try {
        await postExecutionStart({
          caps,
          session_date: sessionDate,
          live_orders: liveOrders,
        })
        await refresh()
        return true
      } catch (err) {
        setActionError(err instanceof Error ? err.message : 'Start failed')
        return false
      } finally {
        setBusy(null)
      }
    },
    [refresh, sessionDate],
  )

  return {
    status,
    positions,
    preflight,
    error,
    actionError,
    busy,
    running,
    refresh,
    start,
    stopEntries: () => sendCommand('stop'),
    startEntries: () => sendCommand('start'),
    closePosition: (tradeId: string) => sendCommand('close_position', tradeId),
    killAll: () => sendCommand('kill_all'),
    clearActionError: () => setActionError(null),
  }
}

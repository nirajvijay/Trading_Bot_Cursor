import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchExecutionCommand,
  fetchExecutionPositions,
  fetchExecutionPreflight,
  fetchExecutionSettings,
  fetchExecutionStatus,
  postExecutionCommand,
  postExecutionSettings,
  postExecutionStart,
} from '../api/client'
import type {
  ExecutionCommand,
  ExecutionCommandKind,
  ExecutionCommandPayload,
  ExecutionPositions,
  ExecutionPreflight,
  ExecutionSessionCaps,
  ExecutionSettings,
  ExecutionStatus,
  StrategyMode,
} from '../api/types'

/** What a start is asked for beyond the caps: the strategy, and ARB's stage. */
export interface StartChoice {
  strategyMode: StrategyMode
  arbRiskScale: number
}

// Matches the engine's own 1s tick while it is live, so the desk is never
// more than about a second behind the loop it is watching.
const RUNNING_POLL_MS = 1000
// Nothing is changing when it is stopped; no reason to hammer the API.
const IDLE_POLL_MS = 5000

export const DEFAULT_CAPS: ExecutionSessionCaps = {
  per_trade_cap_rupees: 50,
  per_trade_cap_vwap_limited_rupees: 25,
  daily_loss_cap_rupees: 150,
  total_capital_rupees: 300000,
  leverage_factor: 5,
}

export function useExecutionEngine(sessionDate: string, enabled: boolean) {
  const [status, setStatus] = useState<ExecutionStatus | null>(null)
  const [positions, setPositions] = useState<ExecutionPositions | null>(null)
  const [preflight, setPreflight] = useState<ExecutionPreflight | null>(null)
  const [settings, setSettings] = useState<ExecutionSettings | null>(null)
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
        const [pre, saved] = await Promise.all([
          fetchExecutionPreflight(),
          fetchExecutionSettings(),
        ])
        setPreflight(pre)
        setSettings(saved)
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
    async (kind: ExecutionCommandKind, tradeId?: string, payload?: ExecutionCommandPayload) => {
      setBusy(tradeId ? `${kind}:${tradeId}` : kind)
      setActionError(null)
      try {
        const command = await postExecutionCommand(kind, tradeId, payload)
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
    async (caps: ExecutionSessionCaps, liveOrders: boolean, choice?: StartChoice) => {
      setBusy('start')
      setActionError(null)
      try {
        await postExecutionStart({
          caps,
          session_date: sessionDate,
          live_orders: liveOrders,
          strategy_mode: choice?.strategyMode ?? 'legacy_vwap',
          ...(choice?.strategyMode === 'arb' ? { arb_risk_scale: choice.arbRiskScale } : {}),
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

  const saveAutostart = useCallback(
    async (strategy: StrategyMode, arbRiskScale: number, arbCapital: number) => {
      setBusy('settings')
      setActionError(null)
      try {
        setSettings(
          await postExecutionSettings({
            autostart_strategy: strategy,
            arb_risk_scale: arbRiskScale,
            arb_total_capital_rupees: arbCapital,
          }),
        )
        return true
      } catch (err) {
        setActionError(err instanceof Error ? err.message : 'Could not save the autostart choice')
        return false
      } finally {
        setBusy(null)
      }
    },
    [],
  )

  return {
    status,
    positions,
    preflight,
    settings,
    saveAutostart,
    error,
    actionError,
    busy,
    running,
    refresh,
    start,
    stopEntries: () => sendCommand('stop'),
    startEntries: () => sendCommand('start'),
    closePosition: (tradeId: string) => sendCommand('close_position', tradeId),
    nudgeStop: (tradeId: string, ticks: 1 | -1) => sendCommand('move_stop', tradeId, { ticks }),
    setTrail: (tradeId: string, enabled: boolean) =>
      sendCommand('set_trail', tradeId, { enabled }),
    killAll: () => sendCommand('kill_all'),
    clearActionError: () => setActionError(null),
  }
}

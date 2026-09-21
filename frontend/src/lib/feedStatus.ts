import type { RunnerStatus } from '../api/types'
import { formatTimeIst } from './format'

export type FeedStatusCode =
  | 'STABLE'
  | 'STALE'
  | 'DISCONNECTED'
  | 'UNKNOWN'
  | 'OFFLINE'
  | 'UNAVAILABLE'

/** Request-level + file-level presence for the observation runner. */
export type RunnerPresence = 'running' | 'stopped' | 'unknown'

export interface FeedStatusView {
  code: FeedStatusCode
  label: string
  detail: string
  tone: 'ok' | 'warn' | 'error' | 'neutral'
  showAlert: boolean
}

export function resolveFeedStatus(
  status: RunnerStatus | null,
  presence: RunnerPresence,
): FeedStatusView {
  if (presence === 'unknown') {
    return {
      code: 'UNAVAILABLE',
      label: 'Status unavailable',
      detail: 'Could not load runner status. Retrying…',
      tone: 'neutral',
      showAlert: false,
    }
  }

  if (presence === 'stopped') {
    return {
      code: 'OFFLINE',
      label: 'Observation off',
      detail: 'Start observation to receive live ticks from Kite.',
      tone: 'neutral',
      showAlert: false,
    }
  }

  const feed = status?.feed_status?.toUpperCase()
  const lastTick = status?.last_tick_time
    ? formatTimeIst(status.last_tick_time)
    : null

  if (feed === 'STABLE') {
    return {
      code: 'STABLE',
      label: 'Live feed stable',
      detail: lastTick ? `Last tick ${lastTick} IST` : 'Receiving live ticks from Kite.',
      tone: 'ok',
      showAlert: false,
    }
  }

  if (feed === 'STALE') {
    return {
      code: 'STALE',
      label: 'Observation Running — Feed Stale',
      detail: lastTick
        ? `No fresh ticks since ${lastTick} IST. Stop and restart observation.`
        : 'No recent ticks. Stop and restart observation.',
      tone: 'warn',
      showAlert: true,
    }
  }

  if (feed === 'DISCONNECTED') {
    return {
      code: 'DISCONNECTED',
      label: 'Feed disconnected',
      detail: 'Kite websocket is down. Restart observation after checking your token.',
      tone: 'error',
      showAlert: true,
    }
  }

  return {
    code: 'UNKNOWN',
    label: 'Observation Running',
    detail: 'Waiting for runner feed details. If this persists, restart observation.',
    tone: 'neutral',
    showAlert: true,
  }
}

export function resolveRunnerPresence(
  status: RunnerStatus | null,
  statusFetchOk: boolean,
): RunnerPresence {
  if (!statusFetchOk) return 'unknown'
  if (status?.runner_state === 'running') return 'running'
  return 'stopped'
}

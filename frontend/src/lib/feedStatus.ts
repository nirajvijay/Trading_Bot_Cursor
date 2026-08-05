import type { RunnerStatus } from '../api/types'
import { formatTimeIst } from './format'

export type FeedStatusCode = 'STABLE' | 'STALE' | 'DISCONNECTED' | 'UNKNOWN' | 'OFFLINE'

export interface FeedStatusView {
  code: FeedStatusCode
  label: string
  detail: string
  tone: 'ok' | 'warn' | 'error' | 'neutral'
  showAlert: boolean
}

export function resolveFeedStatus(
  status: RunnerStatus | null,
  runnerRunning: boolean,
): FeedStatusView {
  if (!runnerRunning) {
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
      label: 'Tick feed stale',
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
    label: 'Feed status unknown',
    detail: 'Waiting for runner status. If this persists, restart observation.',
    tone: 'neutral',
    showAlert: true,
  }
}

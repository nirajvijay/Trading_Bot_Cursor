import type { RunnerStatus, VwapQualifierStatus } from '../api/types'
import type { RunnerPresence } from './feedStatus'

export type VwapQualifierUiState =
  | 'hidden'
  | 'unknown'
  | 'bootstrapping'
  | 'repairing'
  | 'ready'
  | 'unavailable'

export interface VwapQualifierView {
  state: VwapQualifierUiState
  label: string
  detail: string
  tone: 'ok' | 'warn' | 'error' | 'neutral'
  showCounts: boolean
  accept: number
  limited: number
  reject: number
  unavailable: number
}

const DISCLAIMER = 'Observation qualifier only. Not a trading permission.'

function withDisclaimer(reason: string | null | undefined): string {
  const extra = (reason ?? '').trim()
  return extra ? `${DISCLAIMER} ${extra}.` : DISCLAIMER
}

function countsFrom(snapshot: VwapQualifierStatus | null | undefined) {
  return {
    accept: snapshot?.accept ?? 0,
    limited: snapshot?.limited ?? 0,
    reject: snapshot?.reject ?? 0,
    unavailable: snapshot?.unavailable ?? 0,
  }
}

export function resolveVwapQualifierView(
  status: RunnerStatus | null,
  presence: RunnerPresence,
): VwapQualifierView {
  if (presence === 'stopped') {
    return {
      state: 'hidden',
      label: '',
      detail: DISCLAIMER,
      tone: 'neutral',
      showCounts: false,
      accept: 0,
      limited: 0,
      reject: 0,
      unavailable: 0,
    }
  }

  if (presence === 'unknown') {
    return {
      state: 'unknown',
      label: 'VWAP unknown',
      detail: withDisclaimer('status unavailable'),
      tone: 'neutral',
      showCounts: false,
      accept: 0,
      limited: 0,
      reject: 0,
      unavailable: 0,
    }
  }

  const snapshot = status?.vwap_qualifier
  const feed = status?.feed_status?.toUpperCase()
  const feedGlobalFail = feed === 'STALE' || feed === 'DISCONNECTED'

  if (snapshot == null || typeof snapshot.state !== 'string') {
    return {
      state: 'unknown',
      label: 'VWAP unknown',
      detail: withDisclaimer('status missing'),
      tone: 'neutral',
      showCounts: false,
      accept: 0,
      limited: 0,
      reject: 0,
      unavailable: 0,
    }
  }

  if (feedGlobalFail) {
    const reason = feed === 'STALE' ? 'feed stale' : 'feed disconnected'
    return {
      state: 'unavailable',
      label: 'VWAP UNAVAILABLE',
      detail: withDisclaimer(snapshot.reason || reason),
      tone: 'error',
      showCounts: true,
      ...countsFrom(snapshot),
    }
  }

  if (snapshot.state === 'bootstrapping') {
    return {
      state: 'bootstrapping',
      label: 'VWAP BOOTSTRAPPING',
      detail: withDisclaimer(snapshot.reason || 'bootstrapping historical 1m'),
      tone: 'warn',
      showCounts: true,
      ...countsFrom(snapshot),
    }
  }

  if (snapshot.state === 'repairing') {
    return {
      state: 'repairing',
      label: 'VWAP REPAIRING',
      detail: withDisclaimer(snapshot.reason || 'repair pending'),
      tone: 'warn',
      showCounts: true,
      ...countsFrom(snapshot),
    }
  }

  if (snapshot.state === 'unavailable') {
    return {
      state: 'unavailable',
      label: 'VWAP UNAVAILABLE',
      detail: withDisclaimer(snapshot.reason || 'qualifier unavailable'),
      tone: 'error',
      showCounts: true,
      ...countsFrom(snapshot),
    }
  }

  if (snapshot.state === 'ready') {
    return {
      state: 'ready',
      label: 'VWAP READY',
      detail: withDisclaimer(snapshot.reason || 'qualifier ready'),
      tone: 'ok',
      showCounts: true,
      ...countsFrom(snapshot),
    }
  }

  return {
    state: 'unknown',
    label: 'VWAP unknown',
    detail: withDisclaimer('status missing'),
    tone: 'neutral',
    showCounts: false,
    accept: 0,
    limited: 0,
    reject: 0,
    unavailable: 0,
  }
}

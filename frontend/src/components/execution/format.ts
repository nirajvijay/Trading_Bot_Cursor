/** Shared formatting for the Execution Desk. */

export function inr(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const sign = n < 0 ? '-' : ''
  return `${sign}₹${Math.abs(n).toLocaleString('en-IN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  })}`
}

export function num(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toLocaleString('en-IN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  })
}

export function lakhs(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  if (Math.abs(n) >= 100000) return `₹${(n / 100000).toFixed(2)}L`
  return inr(n, 0)
}

/** Ages the desk shows. Anything over a few seconds means something is wrong. */
export function ageLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—'
  if (seconds < 2) return 'live'
  if (seconds < 60) return `${seconds.toFixed(0)}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

export function secondsSince(iso: string | null | undefined): number | null {
  if (!iso) return null
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return null
  return (Date.now() - then) / 1000
}

/** A live mark older than this is shown greyed rather than as fact. */
export const STALE_MARK_SECONDS = 5

export function timeIst(iso: string | null | undefined): string {
  if (!iso) return '—'
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return '—'
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(at)
}

const STATE_LABELS: Record<string, string> = {
  pending_entry: 'Sizing',
  entry_submitted: 'Entry sent',
  entered: 'Filled · no stop',
  protected: 'Protected',
  trailing: 'Trailing',
  exit_submitted: 'Exiting',
  closed: 'Closed',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
}

export function stateLabel(state: string): string {
  return STATE_LABELS[state] ?? state
}

/** Filled with no confirmed stop: the most dangerous state in the system. */
export function isUnprotected(state: string): boolean {
  return state === 'entered' || state === 'entry_submitted'
}

const REASON_LABELS: Record<string, string> = {
  stop_hit: 'Stop hit',
  eod_squareoff: 'EOD square-off',
  daily_loss_breach: 'Daily loss breach',
  manual_close: 'Closed manually',
  kill_all: 'Kill all',
  abnormal_slippage_flatten: 'Abnormal slippage',
  manual_broker_intervention: 'Closed in Kite',
  unattributed: 'Unattributed',
  vwap_reject: 'VWAP reject',
  vwap_unavailable: 'VWAP unavailable',
  vwap_limited: 'VWAP limited',
  sized_to_zero: 'Sized to zero',
  no_structural_stop: 'No structural stop',
  symbol_already_open: 'Symbol already open',
  past_entry_cutoff: 'Past 14:00 cutoff',
  entries_paused: 'Entries paused',
  entries_stopped: 'Entries stopped',
  feed_stale: 'Feed stale',
  insufficient_margin_preflight: 'Insufficient margin',
}

export function reasonLabel(reason: string | null | undefined): string {
  if (!reason) return '—'
  return REASON_LABELS[reason] ?? reason.replace(/_/g, ' ')
}

const PRECONDITION_LABELS: Record<string, string> = {
  market_hours: 'Market open (09:15–15:30 IST)',
  before_entry_cutoff: 'Before 14:00 entry cutoff',
  observation_runner: 'Observation runner active',
  no_engine_running: 'No engine already running',
  risk_config: 'Risk caps are sane',
}

export function preconditionLabel(key: string): string {
  return PRECONDITION_LABELS[key] ?? key.replace(/_/g, ' ')
}

export function pnlClass(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n) || n === 0) {
    return 'text-on-surface-variant'
  }
  return n < 0 ? 'text-negative' : 'text-positive'
}

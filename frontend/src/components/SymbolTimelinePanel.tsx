import type { SymbolTimelineResponse, TimelineEvent, TimelineSetup } from '../api/types'
import { formatPrice, formatTimeIst } from '../lib/format'

type EventTone = 'success' | 'danger' | 'warning' | 'neutral' | 'info'

const STATUS_CONFIG: Record<
  string,
  { label: string; border: string; bg: string; badge: string }
> = {
  triggered: {
    label: 'Triggered',
    border: 'border-l-positive',
    bg: 'bg-green-50/80',
    badge: 'bg-green-100 text-green-800 border-green-200',
  },
  rejected: {
    label: 'Rejected',
    border: 'border-l-negative',
    bg: 'bg-red-50/80',
    badge: 'bg-red-100 text-red-800 border-red-200',
  },
  cancelled: {
    label: 'Cancelled',
    border: 'border-l-slate-400',
    bg: 'bg-slate-50',
    badge: 'bg-slate-200 text-slate-700 border-slate-300',
  },
  expired: {
    label: 'Expired',
    border: 'border-l-slate-300',
    bg: 'bg-slate-50/60',
    badge: 'bg-slate-100 text-slate-600 border-slate-200',
  },
  disarmed: {
    label: 'Disarmed',
    border: 'border-l-slate-300',
    bg: 'bg-slate-50/60',
    badge: 'bg-slate-100 text-slate-600 border-slate-200',
  },
  traded: {
    label: 'Traded',
    border: 'border-l-emerald-500',
    bg: 'bg-emerald-50/80',
    badge: 'bg-emerald-100 text-emerald-800 border-emerald-200',
  },
  active: {
    label: 'In progress',
    border: 'border-l-primary',
    bg: 'bg-sky-50/50',
    badge: 'bg-sky-100 text-sky-800 border-sky-200',
  },
  unknown: {
    label: 'Unknown',
    border: 'border-l-outline-variant',
    bg: 'bg-white',
    badge: 'bg-gray-100 text-gray-600 border-gray-200',
  },
}

const EVENT_TONE_STYLES: Record<
  EventTone,
  { dot: string; icon: string; line: string }
> = {
  success: {
    dot: 'border-positive bg-green-50 text-positive',
    icon: 'text-positive',
    line: 'bg-positive/30',
  },
  danger: {
    dot: 'border-negative bg-red-50 text-negative',
    icon: 'text-negative',
    line: 'bg-negative/30',
  },
  warning: {
    dot: 'border-warning bg-amber-50 text-warning',
    icon: 'text-warning',
    line: 'bg-warning/30',
  },
  info: {
    dot: 'border-primary bg-sky-50 text-primary',
    icon: 'text-primary',
    line: 'bg-primary/20',
  },
  neutral: {
    dot: 'border-outline-variant bg-white text-on-surface-variant',
    icon: 'text-on-surface-variant',
    line: 'bg-outline-variant/60',
  },
}

function getEventTone(eventType: string, resultingState: string): EventTone {
  if (
    eventType === 'CONTINUATION_TRIGGERED' ||
    resultingState === 'CONTINUATION_TRIGGERED' ||
    eventType === 'PULLBACK_READY'
  ) {
    return 'success'
  }
  if (
    eventType === 'CANCELLED' ||
    resultingState === 'CANCELLED' ||
    eventType === 'CONTINUATION_REJECTED' ||
    resultingState === 'CONTINUATION_REJECTED' ||
    eventType === 'INVALIDATED' ||
    resultingState === 'INVALIDATED'
  ) {
    return 'danger'
  }
  if (
    eventType === 'EXPIRED' ||
    resultingState === 'EXPIRED' ||
    eventType === 'SESSION_CLOSED' ||
    resultingState === 'SESSION_CLOSED'
  ) {
    return 'warning'
  }
  if (
    eventType === 'SPIKE_ACCEPTED' ||
    eventType === 'SETUP_CREATED' ||
    eventType === 'CONTINUATION_ATTEMPT'
  ) {
    return 'info'
  }
  return 'neutral'
}

function getEventIcon(eventType: string): string {
  switch (eventType) {
    case 'SETUP_CREATED':
      return 'flag'
    case 'SPIKE_ACCEPTED':
      return 'bolt'
    case 'IMPULSE_BOUNDARIES_FROZEN':
      return 'straighten'
    case 'SPIKE_EXTREME_BREACHED':
      return 'warning'
    case 'PULLBACK_READY':
      return 'check_circle'
    case 'CONTINUATION_ATTEMPT':
      return 'visibility'
    case 'CONTINUATION_TRIGGERED':
      return 'rocket_launch'
    case 'CONTINUATION_REJECTED':
      return 'block'
    case 'CANCELLED':
      return 'cancel'
    case 'INVALIDATED':
      return 'block'
    case 'EXPIRED':
      return 'schedule'
    case 'SESSION_CLOSED':
      return 'logout'
    case 'TRADE_EXECUTED':
      return 'payments'
    default:
      return 'radio_button_checked'
  }
}

function formatStateLabel(state: string): string {
  return state
    .split('_')
    .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
    .join(' ')
}

function DirectionPill({ direction }: { direction: string }) {
  const isUp = direction === 'UP'
  const isDown = direction === 'DOWN'
  return (
    <span
      className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-sm text-[10px] font-bold border ${
        isUp
          ? 'bg-green-50 text-positive border-green-200'
          : isDown
            ? 'bg-red-50 text-negative border-red-200'
            : 'bg-surface-container text-on-surface-variant border-outline-variant'
      }`}
    >
      <span className="material-symbols-outlined text-[14px]">
        {isUp ? 'arrow_upward' : isDown ? 'arrow_downward' : 'remove'}
      </span>
      {isUp ? 'LONG' : isDown ? 'SHORT' : 'FLAT'}
    </span>
  )
}

function OutcomeBadge({ status }: { status: string }) {
  const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.unknown
  return (
    <span
      className={`px-2 py-0.5 text-[10px] font-bold rounded-sm border uppercase tracking-wide ${config.badge}`}
    >
      {config.label}
    </span>
  )
}

function EventTimeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) return null

  return (
    <div className="mt-3 ml-1">
      {events.map((event, index) => {
        const tone = getEventTone(event.event_type, event.resulting_state)
        const styles = EVENT_TONE_STYLES[tone]
        const isLast = index === events.length - 1
        const isTerminal =
          tone === 'success' || tone === 'danger' || tone === 'warning'

        return (
          <div key={`${event.sequence_number}-${event.created_at}`} className="flex gap-3">
            <div className="flex flex-col items-center w-7 shrink-0">
              <div
                className={`w-7 h-7 rounded-full border-2 flex items-center justify-center shrink-0 ${styles.dot} ${
                  isTerminal && isLast ? 'ring-2 ring-offset-1 ring-current/20' : ''
                }`}
              >
                <span className={`material-symbols-outlined text-[15px] ${styles.icon}`}>
                  {getEventIcon(event.event_type)}
                </span>
              </div>
              {!isLast && <div className={`w-0.5 flex-1 min-h-[12px] my-0.5 ${styles.line}`} />}
            </div>

            <div className={`flex-1 min-w-0 ${isLast ? 'pb-0' : 'pb-3'}`}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p
                    className={`text-xs font-semibold leading-snug ${
                      tone === 'success'
                        ? 'text-positive'
                        : tone === 'danger'
                          ? 'text-negative'
                          : 'text-on-surface'
                    }`}
                  >
                    {event.label}
                  </p>
                  <span className="inline-block mt-1 px-1.5 py-px text-[9px] font-medium rounded bg-surface-container text-on-surface-variant border border-outline-variant/60">
                    {formatStateLabel(event.resulting_state)}
                  </span>
                </div>
                <time className="font-data text-[10px] text-on-surface-variant shrink-0 pt-0.5">
                  {formatTimeIst(event.created_at)}
                </time>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function SetupCard({ setup, index, total }: { setup: TimelineSetup; index: number; total: number }) {
  const config = STATUS_CONFIG[setup.status] ?? STATUS_CONFIG.unknown
  const isTerminal = setup.status !== 'active'

  return (
    <article
      className={`rounded-lg border border-outline-variant/80 border-l-4 ${config.border} ${config.bg} overflow-hidden shadow-sm`}
    >
      <header className="px-4 py-3 border-b border-outline-variant/40 bg-white/70">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-bold text-on-surface-variant uppercase tracking-wider">
              Setup {index}
              {total > 1 && (
                <span className="text-on-surface-variant/50 font-normal normal-case">
                  {' '}
                  of {total}
                </span>
              )}
            </span>
            <DirectionPill direction={setup.direction} />
            <OutcomeBadge status={setup.status} />
          </div>
          <time className="font-data text-[10px] text-on-surface-variant">
            {formatTimeIst(setup.created_at)}
          </time>
        </div>

        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-on-surface-variant font-data">
          <span>
            <span className="text-on-surface-variant/60">Spike candle</span>{' '}
            {formatTimeIst(setup.spike_candle_time)}
          </span>
        </div>

        {setup.continuation && (
          <div
            className={`mt-2.5 flex flex-wrap items-center gap-2 rounded-md px-2.5 py-1.5 text-xs ${
              setup.status === 'triggered'
                ? 'bg-green-100/60 border border-green-200'
                : setup.status === 'rejected'
                  ? 'bg-red-100/40 border border-red-200'
                  : 'bg-surface-container border border-outline-variant/60'
            }`}
          >
            <span className="material-symbols-outlined text-[16px] text-on-surface-variant">
              target
            </span>
            <span className="font-data font-semibold text-on-surface">
              ₹{formatPrice(setup.continuation.trigger_price)}
            </span>
            {setup.continuation.decision && (
              <span className="text-on-surface-variant">
                · {setup.continuation.decision}
                {setup.continuation.reason ? ` — ${setup.continuation.reason}` : ''}
              </span>
            )}
          </div>
        )}
      </header>

      <div className="px-4 py-3">
        <p className="label-caps text-on-surface-variant/70 mb-1">Event flow</p>
        <EventTimeline events={setup.events} />
        {isTerminal && setup.events.length > 0 && (
          <p className="mt-2 text-[10px] text-on-surface-variant/80 italic">
            Ended in{' '}
            <span className="font-semibold not-italic">{config.label.toLowerCase()}</span> at{' '}
            {formatTimeIst(setup.events[setup.events.length - 1].created_at)}
          </p>
        )}
      </div>
    </article>
  )
}

function SpikeStrip({ spikes }: { spikes: SymbolTimelineResponse['spikes'] }) {
  if (spikes.length === 0) return null

  return (
    <section>
      <div className="flex items-center gap-2 mb-2">
        <span className="material-symbols-outlined text-[18px] text-amber-600">bolt</span>
        <h3 className="label-caps text-on-surface">Intraday spikes</h3>
        <span className="px-1.5 py-px text-[9px] font-bold rounded-full bg-amber-100 text-amber-800 border border-amber-200">
          {spikes.length}
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {spikes.map((spike) => {
          const isUp = spike.direction === 'UP'
          const isDown = spike.direction === 'DOWN'
          return (
            <div
              key={`${spike.candle_time}-${spike.detected_at}`}
              className={`inline-flex items-center gap-2 px-2.5 py-1.5 rounded-md border text-xs ${
                isUp
                  ? 'bg-green-50 border-green-200'
                  : isDown
                    ? 'bg-red-50 border-red-200'
                    : 'bg-surface-container border-outline-variant'
              }`}
            >
              <span
                className={`material-symbols-outlined text-[16px] ${
                  isUp ? 'text-positive' : isDown ? 'text-negative' : 'text-on-surface-variant'
                }`}
              >
                {isUp ? 'arrow_upward' : isDown ? 'arrow_downward' : 'remove'}
              </span>
              <span className="font-data font-semibold">₹{formatPrice(spike.close)}</span>
              <span className="font-data text-[10px] text-on-surface-variant">
                {formatTimeIst(spike.candle_time)}
              </span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

interface Props {
  symbol: string
  data: SymbolTimelineResponse | null
  loading: boolean
  error: string | null
  onRetry: () => void
}

export function SymbolTimelinePanel({ symbol, data, loading, error, onRetry }: Props) {
  if (loading && !data) {
    return (
      <div className="px-6 py-8 space-y-3 animate-pulse">
        <div className="h-3 w-32 bg-outline-variant/40 rounded" />
        <div className="h-16 bg-outline-variant/20 rounded-lg" />
        <div className="h-24 bg-outline-variant/20 rounded-lg" />
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="px-6 py-5 flex items-center gap-3 text-sm text-negative bg-red-50/50">
        <span className="material-symbols-outlined text-[20px]">error</span>
        <span className="flex-1">{error}</span>
        <button
          type="button"
          onClick={onRetry}
          className="px-3 py-1 text-xs font-semibold text-primary border border-primary/30 rounded hover:bg-primary/5"
        >
          Retry
        </button>
      </div>
    )
  }

  if (!data || (data.spikes.length === 0 && data.setups.length === 0)) {
    return (
      <div className="px-6 py-5 flex items-center gap-2 text-sm text-on-surface-variant">
        <span className="material-symbols-outlined text-[20px]">info</span>
        No spikes or setups recorded for {symbol} this session.
      </div>
    )
  }

  const triggeredCount = data.setups.filter((s) => s.status === 'triggered').length
  const cancelledCount = data.setups.filter((s) => s.status === 'cancelled').length
  const rejectedCount = data.setups.filter((s) => s.status === 'rejected').length

  return (
    <div className="px-5 py-4 bg-gradient-to-b from-surface-container-low to-surface-container border-t-2 border-primary/20">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4 pb-3 border-b border-outline-variant/50">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-[20px] text-primary">history</span>
          <div>
            <h2 className="text-sm font-bold text-on-surface">{symbol} — session history</h2>
            <p className="text-[10px] text-on-surface-variant">
              {data.spikes.length} spike{data.spikes.length !== 1 ? 's' : ''} ·{' '}
              {data.setups.length} setup{data.setups.length !== 1 ? 's' : ''}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {triggeredCount > 0 && (
            <span className="px-2 py-0.5 text-[9px] font-bold rounded-full bg-green-100 text-green-800 border border-green-200">
              {triggeredCount} triggered
            </span>
          )}
          {cancelledCount > 0 && (
            <span className="px-2 py-0.5 text-[9px] font-bold rounded-full bg-slate-200 text-slate-700 border border-slate-300">
              {cancelledCount} cancelled
            </span>
          )}
          {rejectedCount > 0 && (
            <span className="px-2 py-0.5 text-[9px] font-bold rounded-full bg-red-100 text-red-800 border border-red-200">
              {rejectedCount} rejected
            </span>
          )}
        </div>
      </div>

      <div className="space-y-5 max-w-4xl">
        <SpikeStrip spikes={data.spikes} />

        {data.setups.length > 0 && (
          <section>
            <div className="flex items-center gap-2 mb-3">
              <span className="material-symbols-outlined text-[18px] text-primary">
                timeline
              </span>
              <h3 className="label-caps text-on-surface">Pullback setups</h3>
              <span className="text-[10px] text-on-surface-variant">
                oldest → newest
              </span>
            </div>
            <div className="space-y-4">
              {data.setups.map((setup, index) => (
                <SetupCard
                  key={setup.setup_id}
                  setup={setup}
                  index={index + 1}
                  total={data.setups.length}
                />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

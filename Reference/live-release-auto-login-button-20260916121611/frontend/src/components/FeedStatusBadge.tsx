import type { FeedStatusView } from '../lib/feedStatus'

interface Props {
  feed: FeedStatusView
  compact?: boolean
}

const TONE_CLASSES: Record<FeedStatusView['tone'], string> = {
  ok: 'bg-emerald-50 text-positive border-emerald-200',
  warn: 'bg-amber-50 text-amber-800 border-amber-300',
  error: 'bg-red-50 text-negative border-red-200',
  neutral: 'bg-surface-container text-on-surface-variant border-outline-variant',
}

const DOT_CLASSES: Record<FeedStatusView['tone'], string> = {
  ok: 'bg-positive pulse-green',
  warn: 'bg-amber-500',
  error: 'bg-negative',
  neutral: 'bg-on-surface-variant',
}

export function FeedStatusBadge({ feed, compact = false }: Props) {
  return (
    <span
      className={`label-caps font-extrabold px-3 py-1 border rounded flex items-center gap-1.5 ${TONE_CLASSES[feed.tone]}`}
      title={feed.detail}
    >
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${DOT_CLASSES[feed.tone]}`} />
      {feed.label}
      {!compact && feed.code !== 'OFFLINE' && feed.code !== 'UNAVAILABLE' && (
        <span className="font-normal text-[10px] opacity-80 hidden sm:inline">
          · {feed.code}
        </span>
      )}
    </span>
  )
}

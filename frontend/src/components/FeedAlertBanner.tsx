import type { FeedStatusView } from '../lib/feedStatus'

interface Props {
  feed: FeedStatusView
}

export function FeedAlertBanner({ feed }: Props) {
  if (!feed.showAlert) return null

  const toneClass =
    feed.tone === 'error'
      ? 'bg-red-50 border-red-200 text-red-900'
      : feed.tone === 'warn'
        ? 'bg-amber-50 border-amber-300 text-amber-950'
        : 'bg-surface-container border-outline-variant text-on-surface-variant'

  const icon =
    feed.tone === 'error' ? 'error' : feed.tone === 'warn' ? 'warning' : 'info'

  return (
    <div className={`mx-4 mt-2 px-3 py-2 border text-sm shrink-0 flex items-start gap-2 ${toneClass}`}>
      <span className="material-symbols-outlined text-[18px] shrink-0">{icon}</span>
      <div>
        <p className="font-semibold label-caps">{feed.label}</p>
        <p className="text-xs mt-0.5 opacity-90">{feed.detail}</p>
      </div>
    </div>
  )
}

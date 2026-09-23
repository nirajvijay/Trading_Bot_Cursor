import type { VwapQualifierView } from '../lib/vwapQualifierStatus'

interface Props {
  view: VwapQualifierView
}

const TONE_CLASSES: Record<VwapQualifierView['tone'], string> = {
  ok: 'bg-emerald-50 text-positive border-emerald-200',
  warn: 'bg-amber-50 text-amber-800 border-amber-300',
  error: 'bg-red-50 text-negative border-red-200',
  neutral: 'bg-surface-container text-on-surface-variant border-outline-variant',
}

const DOT_CLASSES: Record<VwapQualifierView['tone'], string> = {
  ok: 'bg-positive pulse-green',
  warn: 'bg-amber-500',
  error: 'bg-negative',
  neutral: 'bg-on-surface-variant',
}

export function VwapQualifierBadge({ view }: Props) {
  if (view.state === 'hidden') return null

  return (
    <div className="flex items-center gap-3 flex-wrap">
      <span
        className={`label-caps font-extrabold px-3 py-1 border rounded flex items-center gap-1.5 ${TONE_CLASSES[view.tone]}`}
        title={view.detail}
      >
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${DOT_CLASSES[view.tone]}`} />
        {view.label}
      </span>
      {view.showCounts && (
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="label-caps text-on-surface-variant hidden lg:inline">ACCEPT:</span>
            <span className="label-caps text-on-surface-variant lg:hidden">A</span>
            <span className="font-data text-xs">{view.accept}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="label-caps text-on-surface-variant hidden lg:inline">LIMITED:</span>
            <span className="label-caps text-on-surface-variant lg:hidden">L</span>
            <span className="font-data text-xs">{view.limited}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="label-caps text-on-surface-variant hidden lg:inline">REJECT:</span>
            <span className="label-caps text-on-surface-variant lg:hidden">R</span>
            <span className="font-data text-xs">{view.reject}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="label-caps text-on-surface-variant hidden lg:inline">UNAVAILABLE:</span>
            <span className="label-caps text-on-surface-variant lg:hidden">U</span>
            <span className="font-data text-xs">{view.unavailable}</span>
          </div>
        </div>
      )}
    </div>
  )
}

const PULLBACK_STYLES: Record<string, string> = {
  Setup: 'bg-amber-50 text-amber-800 border-amber-200',
  Impulse: 'bg-amber-100 text-amber-900 border-amber-300',
  Watching: 'bg-sky-100 text-sky-800 border-sky-200',
  Ready: 'bg-emerald-100 text-emerald-800 border-emerald-200',
  Confirmed: 'bg-purple-100 text-purple-800 border-purple-200',
}

const PULLBACK_HINTS: Record<string, string> = {
  Setup: 'Spike accepted — pullback setup created',
  Impulse: 'Measuring impulse move before pullback watch',
  Watching: 'Actively monitoring for pullback',
  Ready: 'Pullback conditions met — awaiting continuation',
  Confirmed: 'Pullback phase complete — continuation stage',
}

interface Props {
  label: string
}

export function PullbackBadge({ label }: Props) {
  if (label === '-' || !label) {
    return <span className="text-on-surface-variant/40">—</span>
  }

  const style = PULLBACK_STYLES[label] ?? 'bg-surface-container text-on-surface-variant border-outline-variant'
  const hint = PULLBACK_HINTS[label]

  return (
    <span
      title={hint}
      className={`px-2 py-0.5 text-[10px] font-semibold rounded-sm border ${style}`}
    >
      {label}
    </span>
  )
}

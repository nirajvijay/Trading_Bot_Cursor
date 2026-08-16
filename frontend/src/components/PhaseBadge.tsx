import type { UiPhase } from '../api/types'

const PHASE_STYLES: Record<UiPhase, string> = {
  IDLE: 'bg-gray-100 text-gray-600 border-gray-200',
  SPIKE_DETECTED: 'bg-amber-100 text-amber-800 border-amber-200',
  PULLBACK_ACTIVE: 'bg-sky-100 text-sky-800 border-sky-200',
  PULLBACK_READY: 'bg-emerald-100 text-emerald-800 border-emerald-200',
  CONTINUATION_ARMED: 'bg-purple-100 text-purple-800 border-purple-200',
  TRIGGERED: 'bg-green-100 text-green-800 border-green-200',
  REJECTED: 'bg-red-100 text-red-800 border-red-200',
  DISARMED: 'bg-slate-100 text-slate-700 border-slate-200',
}

export function PhaseBadge({ phase }: { phase: UiPhase }) {
  return (
    <span
      className={`px-2 py-0.5 text-[10px] font-bold rounded-sm border uppercase ${PHASE_STYLES[phase]}`}
    >
      {phase.replace(/_/g, ' ')}
    </span>
  )
}

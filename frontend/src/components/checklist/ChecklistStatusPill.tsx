import type { ChecklistStatus } from '../../api/types'

const LABELS: Record<ChecklistStatus, string> = {
  not_checked: 'Not Checked',
  ok: 'OK',
  warning: 'Warning',
  failed: 'Failed',
  needs_update: 'Needs Update',
}

const STYLES: Record<ChecklistStatus, string> = {
  not_checked: 'bg-surface-container text-on-surface-variant border-outline-variant',
  ok: 'bg-emerald-50 text-positive border-emerald-200',
  warning: 'bg-amber-50 text-warning border-amber-200',
  failed: 'bg-red-50 text-negative border-red-200',
  needs_update: 'bg-sky-50 text-primary border-sky-200',
}

export function ChecklistStatusPill({
  status,
  message,
}: {
  status: ChecklistStatus
  message?: string | null
}) {
  const showTooltip = message && status !== 'ok' && status !== 'not_checked'
  return (
    <span
      title={showTooltip ? message : undefined}
      className={`font-data text-[10px] font-semibold px-1.5 py-0.5 border rounded-sm ${STYLES[status]} ${
        showTooltip ? 'cursor-help' : ''
      }`}
    >
      {LABELS[status]}
    </span>
  )
}

export function checklistStatusLabel(status: ChecklistStatus): string {
  return LABELS[status]
}

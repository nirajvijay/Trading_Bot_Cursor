import type { ReactNode } from 'react'
import type { ChecklistStatus, GenerateAction } from '../../api/types'
import { ChecklistStatusPill } from './ChecklistStatusPill'
import { CopyCommandButton } from './CopyCommandButton'

function statusMessageStyles(status: ChecklistStatus): string {
  switch (status) {
    case 'warning':
      return 'bg-amber-50 text-warning border-amber-200'
    case 'failed':
      return 'bg-red-50 text-negative border-red-200'
    case 'needs_update':
      return 'bg-sky-50 text-primary border-sky-200'
    default:
      return 'bg-surface-container text-on-surface-variant border-outline-variant'
  }
}

interface Action {
  label: string
  onClick: () => void
  variant?: 'primary' | 'secondary'
  loading?: boolean
}

interface Props {
  icon: string
  title: string
  status: ChecklistStatus
  statusMessage?: string | null
  children: ReactNode
  primaryAction?: Action
  secondaryAction?: Action
  generateAction?: GenerateAction | null
  onGenerate?: () => void
  generating?: boolean
  copyCommand?: string
  copyLabel?: string
}

export function ChecklistCard({
  icon,
  title,
  status,
  statusMessage,
  children,
  primaryAction,
  secondaryAction,
  generateAction,
  onGenerate,
  generating = false,
  copyCommand,
  copyLabel,
}: Props) {
  const showMessage =
    statusMessage && status !== 'ok' && status !== 'not_checked'

  return (
    <div className="bg-white border border-outline-variant flex flex-col min-h-[220px]">
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-outline-variant bg-surface-container-low">
        <div className="flex items-center gap-2 min-w-0">
          <span className="material-symbols-outlined text-primary text-[18px]">{icon}</span>
          <h3 className="label-caps text-on-surface font-bold truncate">{title}</h3>
        </div>
        <ChecklistStatusPill status={status} message={statusMessage} />
      </div>
      <div className="flex-1 px-3 py-2">
        {showMessage && (
          <div
            className={`mb-2 px-2 py-1.5 text-[11px] leading-snug border rounded-sm flex items-start gap-1.5 ${statusMessageStyles(status)}`}
            title={statusMessage}
          >
            <span className="material-symbols-outlined text-[14px] shrink-0 mt-px">info</span>
            <span>{statusMessage}</span>
          </div>
        )}
        {children}
      </div>
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-t border-outline-variant bg-surface-container-low/50">
        {primaryAction && (
          <button
            type="button"
            disabled={primaryAction.loading}
            onClick={primaryAction.onClick}
            className={`px-2.5 py-1 rounded label-caps text-[10px] font-bold border transition-colors disabled:opacity-50 ${
              primaryAction.variant === 'primary'
                ? 'bg-primary text-white border-primary'
                : 'bg-white border-outline-variant hover:bg-surface-container-low'
            }`}
          >
            {primaryAction.loading ? 'Checking…' : primaryAction.label}
          </button>
        )}
        {secondaryAction && (
          <button
            type="button"
            disabled={secondaryAction.loading}
            onClick={secondaryAction.onClick}
            className="px-2.5 py-1 rounded label-caps text-[10px] font-bold border border-outline-variant bg-white hover:bg-surface-container-low transition-colors disabled:opacity-50"
          >
            {secondaryAction.label}
          </button>
        )}
        {generateAction?.available && onGenerate && (
          <button
            type="button"
            disabled={generating}
            title={generateAction.reason ?? undefined}
            onClick={onGenerate}
            className="px-2.5 py-1 rounded label-caps text-[10px] font-bold border border-primary text-primary bg-white hover:bg-sky-50 transition-colors disabled:opacity-50"
          >
            {generating ? 'Generating…' : generateAction.label}
          </button>
        )}
        {copyCommand && <CopyCommandButton command={copyCommand} label={copyLabel} />}
      </div>
    </div>
  )
}

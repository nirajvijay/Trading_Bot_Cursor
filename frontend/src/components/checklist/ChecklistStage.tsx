import type { ReactNode } from 'react'
import type { ChecklistStatus } from '../../api/types'
import { ChecklistStatusPill } from './ChecklistStatusPill'
import { CopyCommandButton } from './CopyCommandButton'

export type StageTone = 'ok' | 'blocked' | 'pending' | 'warning'

function toneFromStatus(status: ChecklistStatus): StageTone {
  if (status === 'ok') return 'ok'
  if (status === 'failed') return 'blocked'
  if (status === 'needs_update' || status === 'warning') return 'warning'
  return 'pending'
}

const TONE_BORDER: Record<StageTone, string> = {
  ok: 'border-emerald-200',
  blocked: 'border-red-200',
  warning: 'border-amber-200',
  pending: 'border-outline-variant',
}

const TONE_HEADER: Record<StageTone, string> = {
  ok: 'bg-emerald-50/40',
  blocked: 'bg-red-50/50',
  warning: 'bg-amber-50/40',
  pending: 'bg-surface-container-low',
}

const TONE_BADGE: Record<StageTone, string> = {
  ok: 'bg-emerald-50 text-positive border-emerald-200',
  blocked: 'bg-red-50 text-negative border-red-200',
  warning: 'bg-amber-50 text-warning border-amber-200',
  pending: 'bg-surface-container text-on-surface-variant border-outline-variant',
}

export function stageBadgeLabel(status: ChecklistStatus, custom?: string | null): string {
  if (custom) return custom
  switch (status) {
    case 'ok':
      return 'OK'
    case 'failed':
      return 'BLOCKED'
    case 'needs_update':
      return 'NEEDS UPDATE'
    case 'warning':
      return 'WARNING'
    default:
      return 'PENDING'
  }
}

interface Action {
  label: string
  onClick: () => void
  variant?: 'primary' | 'secondary' | 'danger'
  loading?: boolean
}

interface Props {
  stageNumber: string
  title: string
  status: ChecklistStatus
  badgeLabel?: string | null
  expanded: boolean
  onToggle: () => void
  statusMessage?: string | null
  children?: ReactNode
  primaryAction?: Action
  secondaryAction?: Action
  generateActionLabel?: string | null
  onGenerate?: () => void
  generating?: boolean
  copyCommand?: string
  copyLabel?: string
}

function actionClass(variant: Action['variant'] = 'secondary'): string {
  if (variant === 'primary') {
    return 'bg-primary text-white border-primary hover:bg-primary/90'
  }
  if (variant === 'danger') {
    return 'bg-negative text-white border-negative hover:bg-negative/90'
  }
  return 'bg-white text-on-surface border-outline-variant hover:bg-surface-container-low'
}

export function ChecklistStage({
  stageNumber,
  title,
  status,
  badgeLabel,
  expanded,
  onToggle,
  statusMessage,
  children,
  primaryAction,
  secondaryAction,
  generateActionLabel,
  onGenerate,
  generating = false,
  copyCommand,
  copyLabel,
}: Props) {
  const tone = toneFromStatus(status)
  const showMessage = statusMessage && status !== 'ok' && status !== 'not_checked'

  return (
    <section
      className={`border rounded-sm bg-white overflow-hidden ${TONE_BORDER[tone]}`}
      data-expanded={expanded ? 'true' : 'false'}
    >
      <div
        className={`flex items-center gap-2 px-3 py-2.5 border-b ${
          expanded ? 'border-outline-variant/70' : 'border-transparent'
        } ${TONE_HEADER[tone]}`}
      >
        <button
          type="button"
          onClick={onToggle}
          className="flex items-center gap-2 min-w-0 flex-1 text-left"
          aria-expanded={expanded}
        >
          <span className="material-symbols-outlined text-[18px] text-on-surface-variant shrink-0">
            {expanded ? 'expand_more' : 'chevron_right'}
          </span>
          <span className="label-caps text-on-surface-variant shrink-0">{stageNumber}</span>
          <h3 className="label-caps text-on-surface font-extrabold truncate tracking-wide">{title}</h3>
          <span
            className={`label-caps px-1.5 py-0.5 border rounded-sm shrink-0 ${TONE_BADGE[tone]}`}
          >
            {stageBadgeLabel(status, badgeLabel)}
          </span>
          <ChecklistStatusPill status={status} message={statusMessage} />
        </button>

        <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
          {primaryAction && (
            <button
              type="button"
              onClick={primaryAction.onClick}
              disabled={primaryAction.loading}
              className={`px-2.5 py-1 rounded label-caps text-[10px] font-bold border disabled:opacity-50 ${actionClass(
                primaryAction.variant,
              )}`}
            >
              {primaryAction.loading ? 'Working…' : primaryAction.label}
            </button>
          )}
          {!expanded && secondaryAction && (
            <button
              type="button"
              onClick={secondaryAction.onClick}
              className={`px-2.5 py-1 rounded label-caps text-[10px] font-bold border ${actionClass(
                secondaryAction.variant,
              )}`}
            >
              {secondaryAction.label}
            </button>
          )}
        </div>
      </div>

      {expanded && (
        <div className="px-3 py-3 space-y-3">
          {showMessage && (
            <div
              className={`px-2.5 py-2 text-[11px] leading-snug border rounded-sm ${
                tone === 'blocked'
                  ? 'bg-red-50 text-negative border-red-200'
                  : tone === 'warning'
                    ? 'bg-amber-50 text-warning border-amber-200'
                    : 'bg-surface-container text-on-surface-variant border-outline-variant'
              }`}
            >
              {statusMessage}
            </div>
          )}

          {children && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-4 gap-y-1">
              {children}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-outline-variant/60">
            {secondaryAction && (
              <button
                type="button"
                onClick={secondaryAction.onClick}
                className={`px-2.5 py-1 rounded label-caps text-[10px] font-bold border ${actionClass(
                  secondaryAction.variant,
                )}`}
              >
                {secondaryAction.label}
              </button>
            )}
            {generateActionLabel && onGenerate && (
              <button
                type="button"
                onClick={onGenerate}
                disabled={generating}
                className={`px-2.5 py-1 rounded label-caps text-[10px] font-bold border disabled:opacity-50 ${
                  tone === 'blocked' ? actionClass('danger') : actionClass('primary')
                }`}
              >
                {generating ? 'Generating…' : generateActionLabel}
              </button>
            )}
            {copyCommand && <CopyCommandButton command={copyCommand} label={copyLabel} />}
          </div>
        </div>
      )}
    </section>
  )
}

export function shouldExpandByDefault(status: ChecklistStatus): boolean {
  return status === 'failed' || status === 'needs_update' || status === 'warning' || status === 'not_checked'
}

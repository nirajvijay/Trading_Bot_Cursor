import type { ReactNode } from 'react'
import type { ChecklistStatus } from '../../api/types'

export type StageTone = 'ok' | 'blocked' | 'pending' | 'warning'

function toneFromStatus(status: ChecklistStatus): StageTone {
  if (status === 'ok') return 'ok'
  if (status === 'failed') return 'blocked'
  if (status === 'needs_update' || status === 'warning') return 'warning'
  return 'pending'
}

const STATUS_PILL: Record<StageTone, { wrap: string; dot: string; text: string }> = {
  ok: {
    wrap: 'bg-[#82f5c1]',
    dot: 'bg-[#006c4a]',
    text: 'text-[#00714e]',
  },
  blocked: {
    wrap: 'bg-[#ffdad6]',
    dot: 'bg-[#ba1a1a]',
    text: 'text-[#93000a]',
  },
  warning: {
    wrap: 'bg-[#ffe08c]/40]',
    dot: 'bg-[#7d5800]',
    text: 'text-[#7d5800]',
  },
  pending: {
    wrap: 'bg-[#e5eeff]',
    dot: 'bg-[#76777d]',
    text: 'text-[#45464d]',
  },
}

export function figmaStatusLabel(status: ChecklistStatus, custom?: string | null): string {
  if (custom) return custom
  switch (status) {
    case 'ok':
      return 'ACTIVE & AUTHENTICATED'
    case 'failed':
      return 'BLOCKED: MISSING FOR TODAY'
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
  iconSrc?: string
}

interface Props {
  stageNumber: string
  title: string
  status: ChecklistStatus
  badgeLabel?: string | null
  expanded: boolean
  onToggle: () => void
  children?: ReactNode
  secondaryAction?: Action
  primaryAction?: Action
}

function actionBtn(action: Action) {
  const base =
    'inline-flex gap-1.5 h-8 items-center justify-center min-w-[140px] px-4 rounded-[2px] text-[12px] leading-[18px] disabled:opacity-50'
  const variant =
    action.variant === 'primary'
      ? 'bg-black text-white'
      : action.variant === 'danger'
        ? 'bg-[#ba1a1a] text-white'
        : 'bg-[#e5eeff] text-[#0b1c30]'
  return (
    <button
      key={action.label}
      type="button"
      onClick={action.onClick}
      disabled={action.loading}
      className={`${base} ${variant}`}
    >
      {action.iconSrc && (
        <img src={action.iconSrc} alt="" className="h-3.5 w-3.5 object-contain" />
      )}
      {action.loading ? 'Working…' : action.label}
    </button>
  )
}

export function ChecklistStage({
  stageNumber,
  title,
  status,
  badgeLabel,
  expanded,
  onToggle,
  children,
  secondaryAction,
  primaryAction,
}: Props) {
  const tone = toneFromStatus(status)
  const pill = STATUS_PILL[tone]
  const borderAccent =
    tone === 'blocked'
      ? 'border-[#ffdad6]/80'
      : tone === 'ok'
        ? 'border-[#e5eeff]'
        : 'border-[#e5e7eb]'

  return (
    <section
      className={`bg-white flex flex-col overflow-hidden rounded-[4px] shadow-[0px_1px_2px_0px_rgba(0,0,0,0.05)] border ${borderAccent}`}
      data-expanded={expanded ? 'true' : 'false'}
    >
      <div className="flex items-center justify-between gap-3 p-3 w-full">
        <button
          type="button"
          onClick={onToggle}
          className="flex flex-1 gap-1.5 items-center min-w-0 text-left"
          aria-expanded={expanded}
        >
          <img
            src="/figma/icon-9.svg"
            alt=""
            className={`h-[6px] w-[9px] shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`}
          />
          <span className="bg-[#e5eeff] px-1.5 py-0.5 rounded-[2px] shrink-0 font-mono text-[11px] font-bold leading-[14px] text-[#0b1c30]">
            {stageNumber}
          </span>
          <div className="flex flex-col gap-1 items-start min-w-0">
            <h3 className="font-bold text-[18px] leading-6 tracking-[-0.45px] text-[#0b1c30] truncate">
              {title}
            </h3>
            <span
              className={`inline-flex gap-1.5 items-center px-1 py-0.5 rounded-xl ${pill.wrap}`}
            >
              <span className={`size-1.5 rounded-full ${pill.dot}`} />
              <span
                className={`font-mono text-[10px] font-semibold tracking-[0.5px] uppercase leading-3 ${pill.text}`}
              >
                {figmaStatusLabel(status, badgeLabel)}
              </span>
            </span>
          </div>
        </button>
        <div className="flex gap-1 items-center shrink-0 flex-wrap justify-end">
          {secondaryAction && actionBtn(secondaryAction)}
          {primaryAction && actionBtn(primaryAction)}
        </div>
      </div>

      {expanded && children && (
        <div className="bg-[rgba(239,244,255,0.6)] border-t border-[#e5eeff] flex flex-col items-start pb-1.5 pt-[7px] px-3 w-full">
          <div className="flex gap-1.5 items-stretch justify-center py-1 w-full flex-wrap lg:flex-nowrap">
            {children}
          </div>
        </div>
      )}
    </section>
  )
}

export function StageMetricCard({
  label,
  badge,
  children,
  danger = false,
}: {
  label: string
  badge?: ReactNode
  children: ReactNode
  danger?: boolean
}) {
  return (
    <div
      className={`bg-white border flex flex-col items-start justify-between p-[7px] rounded-[2px] flex-1 min-w-[200px] self-stretch ${
        danger ? 'border-[rgba(255,218,214,0.8)]' : 'border-[#e5eeff]'
      }`}
    >
      <div className="flex items-center justify-between w-full gap-2">
        <span className="font-mono text-[10px] font-semibold tracking-[0.5px] uppercase text-[#76777d] leading-[15px]">
          {label}
        </span>
        {badge}
      </div>
      <div className="flex flex-col gap-0.5 items-start w-full mt-1">{children}</div>
    </div>
  )
}

export function shouldExpandByDefault(status: ChecklistStatus): boolean {
  return status === 'failed' || status === 'needs_update' || status === 'warning' || status === 'not_checked'
}

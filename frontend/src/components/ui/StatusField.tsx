import type { ReactNode } from 'react'

export function StatusField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5 border-b border-outline-variant/60 last:border-0">
      <span className="label-caps text-on-surface-variant">{label}</span>
      <div className="text-right shrink-0 text-xs font-data text-on-surface">{children}</div>
    </div>
  )
}

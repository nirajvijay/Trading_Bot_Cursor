export type BadgeValue = 'YES' | 'NO' | 'UNKNOWN'

export function StatusBadge({ value }: { value: BadgeValue }) {
  const styles: Record<BadgeValue, string> = {
    YES: 'bg-emerald-50 text-positive border-emerald-200',
    NO: 'bg-red-50 text-negative border-red-200',
    UNKNOWN: 'bg-surface-container text-on-surface-variant border-outline-variant',
  }
  return (
    <span className={`font-data text-[10px] font-semibold px-1.5 py-0.5 border ${styles[value]}`}>
      [{value}]
    </span>
  )
}

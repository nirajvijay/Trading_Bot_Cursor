export function formatPrice(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return '-'
  return value.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function formatPercent(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return '-'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

export function formatVolume(value?: number | null): string {
  if (value == null) return '-'
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(2)}K`
  return String(value)
}

export function formatDistance(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return '-'
  return `${value >= 0 ? '' : ''}${value.toFixed(2)}% away`
}

export function formatTimeIst(iso?: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export function formatDateTimeIst(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const formatted = d.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
  return `${formatted} IST`
}

export function marketStatusNow(): 'OPEN' | 'CLOSED' {
  const now = new Date()
  const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
  const day = ist.getDay()
  if (day === 0 || day === 6) return 'CLOSED'
  const minutes = ist.getHours() * 60 + ist.getMinutes()
  const open = 9 * 60 + 15
  const close = 15 * 60 + 30
  return minutes >= open && minutes < close ? 'OPEN' : 'CLOSED'
}

export function todayIst(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
}

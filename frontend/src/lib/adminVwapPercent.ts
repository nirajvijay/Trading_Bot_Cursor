/** Convert backend VWAP gap ratios to UI percentages (0.0022 → 0.22). */

export function ratioToPercent(ratio: number): number {
  return ratio * 100
}

export function percentToRatio(percent: number): number {
  return percent / 100
}

export function formatVwapPercent(ratio: number): string {
  const pct = ratioToPercent(ratio)
  const decimals = pct < 1 ? 4 : 2
  return pct.toFixed(decimals)
}

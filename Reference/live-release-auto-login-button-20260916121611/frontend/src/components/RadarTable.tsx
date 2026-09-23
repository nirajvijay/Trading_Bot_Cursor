import { Fragment } from 'react'
import type { RadarRow } from '../api/types'
import {
  formatDistance,
  formatPercent,
  formatPrice,
  formatTimeIst,
  formatVolume,
} from '../lib/format'
import { PhaseBadge } from './PhaseBadge'
import { PullbackBadge } from './PullbackBadge'
import { SymbolTimelinePanel } from './SymbolTimelinePanel'
import { useSymbolTimeline } from '../hooks/useSymbolTimeline'

const COLUMN_COUNT = 13

const COLUMN_HINTS: Record<string, string> = {
  Phase: 'Current strategy stage for this symbol',
  Spike: 'Whether an intraday spike was confirmed on the 1m candle',
  Pullback:
    'Pullback pipeline stage: Setup → Impulse → Watching → Ready (not the same as Phase)',
  Continuation: 'Continuation arm / trigger status after pullback',
  'Last Event': 'Most recent strategy event from the observation runner',
}

function DirectionIcon({ direction }: { direction?: string | null }) {
  if (direction === 'UP') {
    return (
      <span className="material-symbols-outlined text-positive text-[18px]">arrow_upward</span>
    )
  }
  if (direction === 'DOWN') {
    return (
      <span className="material-symbols-outlined text-negative text-[18px]">arrow_downward</span>
    )
  }
  return <span className="material-symbols-outlined text-on-surface-variant/30 text-[18px]">remove</span>
}

function pctClass(value?: number | null): string {
  if (value == null) return 'text-on-surface-variant'
  if (value > 0) return 'text-positive'
  if (value < 0) return 'text-negative'
  return 'text-on-surface-variant'
}

interface Props {
  rows: RadarRow[]
  loading: boolean
  sessionDate: string
  expandedSymbol: string | null
  onRowClick: (symbol: string) => void
  timelineRefreshToken?: number
}

function ExpandedTimelineRow({
  sessionDate,
  symbol,
  timelineRefreshToken,
}: {
  sessionDate: string
  symbol: string
  timelineRefreshToken: number
}) {
  const { data, loading, error, refresh } = useSymbolTimeline(
    sessionDate,
    symbol,
    timelineRefreshToken,
  )

  return (
    <tr className="bg-surface-container-low shadow-[inset_0_4px_12px_rgba(0,0,0,0.04)]">
      <td colSpan={COLUMN_COUNT} className="p-0 border-b-2 border-primary/10">
        <SymbolTimelinePanel
          symbol={symbol}
          data={data}
          loading={loading}
          error={error}
          onRetry={() => void refresh()}
        />
      </td>
    </tr>
  )
}

export function RadarTable({
  rows,
  loading,
  sessionDate,
  expandedSymbol,
  onRowClick,
  timelineRefreshToken = 0,
}: Props) {
  if (loading && rows.length === 0) {
    return (
      <div className="p-8 text-center text-on-surface-variant text-sm">
        Loading observation data...
      </div>
    )
  }

  if (!loading && rows.length === 0) {
    return (
      <div className="p-8 text-center text-on-surface-variant text-sm">
        No observation data for this session. Start the observation runner.
      </div>
    )
  }

  const handleRowKeyDown = (symbol: string, event: React.KeyboardEvent<HTMLTableRowElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onRowClick(symbol)
    }
  }

  return (
    <div className="flex-1 overflow-auto custom-scrollbar">
      <table className="w-full text-left border-collapse">
        <thead className="sticky top-0 bg-white z-20 shadow-[0_1px_0_rgba(0,0,0,0.1)]">
          <tr className="bg-surface-container-low">
            {[
              'Symbol',
              'Last 1m Close',
              '% Change',
              'Phase',
              'Direction',
              'Spike',
              'Pullback',
              'Continuation',
              'Volume',
              'Trigger Price',
              'Distance',
              'Last Event',
              'Updated',
            ].map((col) => (
              <th
                key={col}
                className="px-3 py-2.5 label-caps text-on-surface border-b border-outline-variant"
                title={
                  COLUMN_HINTS[col] ??
                  (col === 'Last 1m Close'
                    ? 'Not tick-level; latest completed 1-minute candle close'
                    : undefined)
                }
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const isExpanded = expandedSymbol === row.symbol
            const hasHistory = (row.setup_count ?? 0) > 1

            return (
              <Fragment key={row.symbol}>
                <tr
                  key={row.symbol}
                  className={`terminal-row hover:bg-primary/5 transition-colors cursor-pointer ${
                    isExpanded ? 'bg-primary/5' : ''
                  }`}
                  onClick={() => onRowClick(row.symbol)}
                  onKeyDown={(event) => handleRowKeyDown(row.symbol, event)}
                  tabIndex={0}
                  aria-expanded={isExpanded}
                >
                  <td className="px-3 py-2 font-data text-xs font-bold">
                    <span className="inline-flex items-center gap-1">
                      <span
                        className={`material-symbols-outlined text-[16px] text-on-surface-variant transition-transform ${
                          isExpanded ? 'rotate-90' : ''
                        }`}
                      >
                        chevron_right
                      </span>
                      {row.symbol}
                      {hasHistory && (
                        <span
                          className="px-1 py-0.5 text-[9px] font-bold rounded-sm bg-secondary-container text-on-secondary-container"
                          title={`${row.setup_count} setups this session`}
                        >
                          ×{row.setup_count}
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-data text-xs">{formatPrice(row.last_1m_close)}</td>
                  <td className={`px-3 py-2 font-data text-xs ${pctClass(row.pct_change)}`}>
                    {formatPercent(row.pct_change)}
                  </td>
                  <td className="px-3 py-2">
                    <PhaseBadge phase={row.phase} />
                  </td>
                  <td className="px-3 py-2 text-center">
                    <DirectionIcon direction={row.direction} />
                  </td>
                  <td className="px-3 py-2 text-xs italic text-on-surface-variant">{row.spike}</td>
                  <td className="px-3 py-2">
                    <PullbackBadge label={row.pullback} />
                  </td>
                  <td className="px-3 py-2 text-xs italic text-on-surface-variant">
                    {row.continuation}
                  </td>
                  <td className="px-3 py-2 font-data text-xs text-right">
                    {formatVolume(row.volume)}
                  </td>
                  <td className="px-3 py-2 font-data text-xs">
                    {row.trigger_price != null ? `₹${formatPrice(row.trigger_price)}` : '-'}
                  </td>
                  <td className="px-3 py-2 font-data text-xs text-warning">
                    {row.distance_pct != null ? formatDistance(row.distance_pct) : '-'}
                  </td>
                  <td className="px-3 py-2 text-xs text-on-surface">{row.last_event}</td>
                  <td className="px-3 py-2 font-data text-xs text-right text-on-surface-variant">
                    {formatTimeIst(row.updated_at)}
                  </td>
                </tr>
                {isExpanded && (
                  <ExpandedTimelineRow
                    sessionDate={sessionDate}
                    symbol={row.symbol}
                    timelineRefreshToken={timelineRefreshToken}
                  />
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

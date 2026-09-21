import { useEffect, useMemo, useState } from 'react'
import type { ObservationReadiness, RadarRow, UiPhase } from '../api/types'
import { fetchSectorMap, type SectorMap } from '../api/client'
import { formatPrice, formatPercent } from '../lib/format'
import { SymbolTimelinePanel } from './SymbolTimelinePanel'
import { useSymbolTimeline } from '../hooks/useSymbolTimeline'
import type { RunnerPresence } from '../lib/feedStatus'
import './RadarHeatMap.css'

type CardStatus = 'WAITING' | 'SPIKE' | 'SETUP_READY' | 'ARMED' | 'TRIGGERED' | 'REJECTED' | 'NEGATED'

const STATUSES: CardStatus[] = ['WAITING', 'SPIKE', 'SETUP_READY', 'ARMED', 'TRIGGERED', 'REJECTED', 'NEGATED']

const STATUS_LABEL: Record<CardStatus, string> = {
  WAITING: 'Waiting',
  SPIKE: 'Spike',
  SETUP_READY: 'Setup Ready',
  ARMED: 'Armed',
  TRIGGERED: 'Triggered',
  REJECTED: 'Rejected',
  NEGATED: 'Negated',
}

// TODO(human): the backend's UiPhase values don't map 1:1 onto the design's
// card-status vocabulary above. This mapping decides what color/state every
// card on the grid shows, so it needs your judgment rather than a guess.
//
// Confirmed mapping (owner consolidation of the engine's 8 phases into the
// design's 7 card statuses; SPIKE_DETECTED and PULLBACK_ACTIVE both read as
// SPIKE since neither has a confirmed setup yet):
function mapPhaseToStatus(phase: UiPhase): CardStatus {
  switch (phase) {
    case 'IDLE':
      return 'WAITING'
    case 'SPIKE_DETECTED':
    case 'PULLBACK_ACTIVE':
      return 'SPIKE'
    case 'PULLBACK_READY':
      return 'SETUP_READY'
    case 'CONTINUATION_ARMED':
      return 'ARMED'
    case 'TRIGGERED':
      return 'TRIGGERED'
    case 'REJECTED':
      return 'REJECTED'
    case 'DISARMED':
      return 'NEGATED'
  }
}

interface Card {
  row: RadarRow
  status: CardStatus
  sector: string | null
  distance: number | null
  updatedSecAgo: number | null
}

function buildCard(row: RadarRow, sectorOf: Map<string, string>): Card {
  const distance =
    row.trigger_price != null && row.last_1m_close != null
      ? Math.abs(row.trigger_price - row.last_1m_close)
      : null
  const updatedSecAgo = row.updated_at
    ? Math.max(0, Math.round((Date.now() - new Date(row.updated_at).getTime()) / 1000))
    : null
  return {
    row,
    status: mapPhaseToStatus(row.phase),
    sector: sectorOf.get(row.symbol) ?? null,
    distance,
    updatedSecAgo,
  }
}

function DetailModal({ symbol, sessionDate, onClose }: { symbol: string; sessionDate: string; onClose: () => void }) {
  const { data, loading, error, refresh } = useSymbolTimeline(sessionDate, symbol, 0)
  return (
    <div className="rhm-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="rhm-modal">
        <div className="rhm-modal-head">
          <span className="rhm-modal-symbol">{symbol}</span>
          <button className="rhm-modal-close" type="button" onClick={onClose}>
            ×
          </button>
        </div>
        <SymbolTimelinePanel symbol={symbol} data={data} loading={loading} error={error} onRetry={() => void refresh()} />
      </div>
    </div>
  )
}

export function RadarHeatMap({
  rows,
  loading,
  search,
  sessionDate,
  observationReadiness = null,
  runnerPresence = 'unknown',
  startingObservation = false,
  stoppingObservation = false,
  observationError = null,
  onStartObservation,
  onStopObservation,
}: {
  rows: RadarRow[]
  loading: boolean
  search: string
  sessionDate: string
  observationReadiness?: ObservationReadiness | null
  runnerPresence?: RunnerPresence
  startingObservation?: boolean
  stoppingObservation?: boolean
  observationError?: string | null
  onStartObservation?: () => void
  onStopObservation?: () => void
}) {
  const [sectorMap, setSectorMap] = useState<SectorMap | null>(null)
  const [activeFilter, setActiveFilter] = useState<CardStatus | 'ALL'>('ALL')
  const [activeSector, setActiveSector] = useState('ALL')
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    fetchSectorMap()
      .then((value) => {
        if (active) setSectorMap(value)
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [])

  const sectorOf = useMemo(() => {
    const map = new Map<string, string>()
    sectorMap?.sectors.forEach((group) => group.symbols.forEach((symbol) => map.set(symbol, group.name)))
    return map
  }, [sectorMap])

  const cards = useMemo(() => rows.map((row) => buildCard(row, sectorOf)), [rows, sectorOf])

  const counts = useMemo(() => {
    const c: Record<CardStatus, number> = { WAITING: 0, SPIKE: 0, SETUP_READY: 0, ARMED: 0, TRIGGERED: 0, REJECTED: 0, NEGATED: 0 }
    cards.forEach((card) => c[card.status]++)
    return c
  }, [cards])

  const searchTerm = search.trim().toUpperCase()

  const runnerRunning = runnerPresence === 'running'
  const canStart = observationReadiness?.can_start ?? false
  const startDisabled = runnerRunning || !canStart || startingObservation || runnerPresence === 'unknown'

  return (
    <div className="radar-heat-map">
      <div className="rhm-header">
        {runnerRunning ? (
          <button
            type="button"
            className="rhm-observation-status running"
            onClick={onStopObservation}
            disabled={stoppingObservation}
            title="Stop the observation runner"
          >
            <span className="rhm-brand-dot" />
            {stoppingObservation ? 'Stopping…' : 'Observation running · Stop'}
          </button>
        ) : (
          <button
            type="button"
            className="rhm-start-btn"
            onClick={onStartObservation}
            disabled={startDisabled}
            title={!canStart ? (observationReadiness?.reason ?? 'Not ready to start') : 'Start the observation runner'}
          >
            {startingObservation ? 'Starting…' : 'Start Observation'}
          </button>
        )}
        {loading && <span className="rhm-refreshing">Refreshing…</span>}
        <select className="rhm-sector-select" value={activeSector} onChange={(e) => setActiveSector(e.target.value)}>
          <option value="ALL">All sectors</option>
          {(sectorMap?.sectors ?? []).map((group) => (
            <option key={group.name} value={group.name}>
              {group.name} ({group.symbols.length})
            </option>
          ))}
        </select>
        <div className="rhm-filters">
          <div className={`rhm-chip${activeFilter === 'ALL' ? ' active' : ''}`} data-status="ALL" onClick={() => setActiveFilter('ALL')}>
            All {rows.length}
          </div>
          {STATUSES.map((status) => (
            <div
              key={status}
              className={`rhm-chip${activeFilter === status ? ' active' : ''}`}
              data-status={status}
              onClick={() => setActiveFilter(status)}
            >
              {STATUS_LABEL[status]}
            </div>
          ))}
        </div>
        <div className="rhm-stats">
          <div className="rhm-stat armed">
            <div className="rhm-stat-num">{counts.ARMED}</div>
            <div className="rhm-stat-label">Armed</div>
          </div>
          <div className="rhm-stat triggered">
            <div className="rhm-stat-num">{counts.TRIGGERED}</div>
            <div className="rhm-stat-label">Triggered</div>
          </div>
          <div className="rhm-stat rejected">
            <div className="rhm-stat-num">{counts.REJECTED}</div>
            <div className="rhm-stat-label">Rejected</div>
          </div>
        </div>
      </div>

      {observationError && <div className="rhm-observation-error">{observationError}</div>}

      <div className="rhm-grid-wrap">
        <div className="rhm-grid">
          {cards.map((card) => {
            const matchesSearch = !searchTerm || card.row.symbol.includes(searchTerm)
            const matchesFilter = activeFilter === 'ALL' || card.status === activeFilter
            const matchesSector = activeSector === 'ALL' || card.sector === activeSector
            const dim = !(matchesSearch && matchesFilter && matchesSector)
            const pct = card.row.pct_change ?? 0
            return (
              <div
                key={card.row.symbol}
                className={`rhm-card ${card.status}${dim ? ' rhm-dim' : ''}`}
                tabIndex={0}
                onClick={() => setSelected(card.row.symbol)}
                onKeyDown={(e) => e.key === 'Enter' && setSelected(card.row.symbol)}
              >
                <div className="rhm-card-top">
                  <span className="rhm-symbol">{card.row.symbol}</span>
                  <span className="rhm-dot" />
                </div>
                <div className="rhm-card-mid">
                  <span className="rhm-price">{formatPrice(card.row.last_1m_close)}</span>
                  <span className={`rhm-change ${pct >= 0 ? 'pos' : 'neg'}`}>{formatPercent(card.row.pct_change)}</span>
                </div>
                <div className="rhm-card-bottom">
                  <span className="rhm-status-group">
                    <span className="rhm-status-label">{STATUS_LABEL[card.status]}</span>
                    {(card.row.setup_count ?? 0) > 1 && (
                      <span className="rhm-setup-count" title={`${card.row.setup_count} setups this session`}>
                        ×{card.row.setup_count}
                      </span>
                    )}
                  </span>
                  {card.distance != null ? (
                    <span className="rhm-distance" title="Distance to trigger price">
                      Δ{formatPrice(card.distance)}
                    </span>
                  ) : (
                    <span className="rhm-updated">{card.updatedSecAgo != null ? `${card.updatedSecAgo}s ago` : '—'}</span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {selected && <DetailModal symbol={selected} sessionDate={sessionDate} onClose={() => setSelected(null)} />}
    </div>
  )
}

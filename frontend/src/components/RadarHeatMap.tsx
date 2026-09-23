import { useEffect, useMemo, useState } from 'react'
import type { ObservationReadiness, RadarRow, UiPhase } from '../api/types'
import { fetchSectorMap, type SectorMap } from '../api/client'
import { formatPrice, formatPercent } from '../lib/format'
import { SymbolTimelinePanel } from './SymbolTimelinePanel'
import { useSymbolTimeline } from '../hooks/useSymbolTimeline'
import type { RunnerPresence } from '../lib/feedStatus'
import './RadarHeatMap.css'

type CardStatus = 'WAITING' | 'SPIKE' | 'SETUP_READY' | 'TRIGGERED' | 'REJECTED' | 'NEGATED'

const STATUSES: CardStatus[] = ['WAITING', 'SPIKE', 'SETUP_READY', 'TRIGGERED', 'REJECTED', 'NEGATED']

// Fixed, high-separation palette for the 17 sector groups. The stronger
// saturation/value keeps adjacent sector bands distinguishable at a glance.
const SECTOR_BACKGROUND_COLORS = [
  '#f3b4b4', '#f4c48a', '#f2df86', '#c8e58a', '#9ddd9d', '#8fd8c8',
  '#8fcfe5', '#91b9e8', '#9fa9e8', '#b99fe2', '#d09cdd', '#e0a1c4',
  '#e9aaa2', '#d7c28a', '#a9d28b', '#8bcfcf', '#a8b8e6',
]

const STATUS_LABEL: Record<CardStatus, string> = {
  WAITING: 'Waiting',
  SPIKE: 'Spike',
  SETUP_READY: 'Setup Ready',
  TRIGGERED: 'Triggered',
  REJECTED: 'Rejected',
  NEGATED: 'Negated',
}

// Owner consolidation of the engine's 8 phases into 6 frontend-facing
// statuses. CONTINUATION_ARMED folds into SETUP_READY — the frontend no
// longer distinguishes "confirmed setup" from "armed for continuation";
// both read as SETUP_READY until the trade actually TRIGGERED.
function mapPhaseToStatus(phase: UiPhase): CardStatus {
  switch (phase) {
    case 'IDLE':
      return 'WAITING'
    case 'SPIKE_DETECTED':
    case 'PULLBACK_ACTIVE':
      return 'SPIKE'
    case 'PULLBACK_READY':
    case 'CONTINUATION_ARMED':
      return 'SETUP_READY'
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
}

function buildCard(row: RadarRow, sectorOf: Map<string, string>): Card {
  return {
    row,
    status: mapPhaseToStatus(row.phase),
    sector: sectorOf.get(row.symbol) ?? null,
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
  sessionTriggered,
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
  sessionTriggered?: number | null
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

  // Stable, evenly-spaced sector hue, so the gutters form visible background
  // blocks behind contiguous cards in the same sector.
  const sectorTint = useMemo(() => {
    const names = (sectorMap?.sectors ?? []).map((group) => group.name).sort((a, b) => a.localeCompare(b))
    const map = new Map<string, string>()
    names.forEach((name, i) => {
      map.set(name, SECTOR_BACKGROUND_COLORS[i % SECTOR_BACKGROUND_COLORS.length])
    })
    return map
  }, [sectorMap])

  const cards = useMemo(() => {
    const built = rows.map((row) => buildCard(row, sectorOf))
    return built.slice().sort((a, b) => {
      const sa = a.sector ?? '￿'
      const sb = b.sector ?? '￿'
      return sa === sb ? a.row.symbol.localeCompare(b.row.symbol) : sa.localeCompare(sb)
    })
  }, [rows, sectorOf])

  const counts = useMemo(() => {
    const c: Record<CardStatus, number> = { WAITING: 0, SPIKE: 0, SETUP_READY: 0, TRIGGERED: 0, REJECTED: 0, NEGATED: 0 }
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
          <div className="rhm-stat triggered">
            <div className="rhm-stat-num">{sessionTriggered ?? counts.TRIGGERED}</div>
            <div className="rhm-stat-label">Triggered (session)</div>
          </div>
          <div className="rhm-stat rejected">
            <div className="rhm-stat-num">{counts.REJECTED}</div>
            <div className="rhm-stat-label">Rejected (current)</div>
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
            const tint = card.sector ? sectorTint.get(card.sector) : undefined
            const hoverDetails = `${formatPrice(card.row.last_1m_close)} · ${formatPercent(card.row.pct_change)}`
            return (
              <div
                key={card.row.symbol}
                className="rhm-sector-slot"
                style={{ background: tint ?? 'transparent' }}
              >
                <div
                  className={`rhm-card ${card.status}${dim ? ' rhm-dim' : ''}`}
                  tabIndex={0}
                  onClick={() => setSelected(card.row.symbol)}
                  onKeyDown={(e) => e.key === 'Enter' && setSelected(card.row.symbol)}
                >
                  <div className="rhm-card-top">
                    <span className="rhm-symbol">{card.row.symbol}</span>
                    <span className="rhm-card-top-dots">
                      {card.row.vwap_classification && (
                        <span
                          className={`rhm-vwap-dot ${card.row.vwap_classification}`}
                          title={`VWAP: ${card.row.vwap_classification.charAt(0)}${card.row.vwap_classification.slice(1).toLowerCase()}`}
                        />
                      )}
                      <span className="rhm-dot" />
                    </span>
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
                  </div>
                  <span className="rhm-hover-tooltip" role="tooltip">
                    {hoverDetails}
                  </span>
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

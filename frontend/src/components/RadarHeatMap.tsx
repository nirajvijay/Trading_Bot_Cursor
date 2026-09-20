import { useState, useMemo } from 'react'
import type { RadarRow } from '../api/types'

interface StockData {
  symbol: string
  sector: string
  status: 'WAITING' | 'SPIKE' | 'SETUP_READY' | 'ARMED' | 'TRIGGERED' | 'REJECTED' | 'NEGATED'
  price: number | null
  changePct: number | null
  updatedSec: number
  distance: number | null
  timeline: string[]
}

const SECTORS = [
  'All sectors',
  'Automobile and Auto Components',
  'Capital Goods',
  'Chemicals',
  'Construction',
  'Construction Materials',
  'Consumer Durables',
  'Consumer Services',
  'Fast Moving Consumer Goods',
  'Financial Services',
  'Healthcare',
  'Information Technology',
  'Metals & Mining',
  'Oil, Gas & Consumable Fuels',
  'Power',
  'Realty',
  'Services',
  'Telecommunication',
]

const SECTOR_STOCKS: Record<string, string[]> = {
  'Automobile and Auto Components': ['MARUTI', 'M&M', 'BAJAJ-AUTO', 'EICHERMOT', 'TVSMOTOR', 'HYUNDAI', 'MOTHERSON', 'BOSCHLTD', 'TMPV'],
  'Capital Goods': ['HAL', 'BEL', 'TMCV', 'ABB', 'SIEMENS', 'CGPOWER', 'CUMMINSIND', 'ENRIN', 'MAZDOCK'],
  'Chemicals': ['SOLARINDS', 'PIDILITIND'],
  'Construction': ['LT'],
  'Construction Materials': ['ULTRACEMCO', 'GRASIM', 'AMBUJACEM', 'SHREECEM'],
  'Consumer Durables': ['TITAN', 'ASIANPAINT'],
  'Consumer Services': ['ETERNAL', 'DMART', 'TRENT', 'INDHOTEL'],
  'Fast Moving Consumer Goods': ['HINDUNILVR', 'ITC', 'NESTLEIND', 'VBL', 'BRITANNIA', 'UNITDSPR', 'TATACONSUM', 'GODREJCP'],
  'Financial Services': ['HDFCBANK', 'ICICIBANK', 'SBIN', 'BAJFINANCE', 'KOTAKBANK', 'AXISBANK', 'BAJAJFINSV', 'SHRIRAMFIN', 'SBILIFE', 'TATACAP', 'JIOFIN', 'CHOLAFIN', 'UNIONBANK', 'PNB', 'BAJAJHLDNG', 'BANKBARODA', 'HDFCLIFE', 'PFC', 'MUTHOOTFIN', 'CANBK', 'IRFC', 'HDFCAMC', 'RECLTD'],
  'Healthcare': ['SUNPHARMA', 'DIVISLAB', 'TORNTPHARM', 'APOLLOHOSP', 'CIPLA', 'ZYDUSLIFE', 'DRREDDY', 'MAXHEALTH'],
  'Information Technology': ['TCS', 'INFY', 'HCLTECH', 'WIPRO', 'TECHM', 'LTM'],
  'Metals & Mining': ['ADANIENT', 'JSWSTEEL', 'HINDZINC', 'TATASTEEL', 'HINDALCO', 'JINDALSTEL', 'VEDL'],
  'Oil, Gas & Consumable Fuels': ['RELIANCE', 'ONGC', 'COALINDIA', 'IOC', 'BPCL', 'GAIL'],
  'Power': ['ADANIPOWER', 'NTPC', 'POWERGRID', 'ADANIGREEN', 'ADANIENSOL', 'TATAPOWER'],
  'Realty': ['DLF', 'LODHA'],
  'Services': ['ADANIPORTS', 'INDIGO'],
  'Telecommunication': ['BHARTIARTL'],
}

const STATUS_COLORS: Record<string, string> = {
  'WAITING': '#9E9E9E',
  'SPIKE': '#FBC02D',
  'SETUP_READY': '#FFA726',
  'ARMED': '#66BB6A',
  'TRIGGERED': '#2E7D32',
  'REJECTED': '#D32F2F',
  'NEGATED': '#B71C1C',
}

const PHASE_TO_STATUS: Record<string, StockData['status']> = {
  'WAITING': 'WAITING',
  'SPIKE': 'SPIKE',
  'SETUP_READY': 'SETUP_READY',
  'CONTINUATION_ARMED': 'ARMED',
  'PULLBACK_ARMED': 'ARMED',
  'TRIGGERED': 'TRIGGERED',
  'REJECTED': 'REJECTED',
  'NEGATED': 'NEGATED',
}

export function RadarHeatMap({ rows, loading, search }: {
  rows: RadarRow[]; loading: boolean; search: string
}) {
  const [selectedSector, setSelectedSector] = useState('All sectors')
  const [selectedStatus, setSelectedStatus] = useState<string | null>(null)
  const [selectedStock, setSelectedStock] = useState<StockData | null>(null)

  const stockMap = useMemo(() => {
    const map = new Map<string, StockData>()
    rows.forEach(row => {
      let sector = 'Other'
      for (const [sectorName, symbols] of Object.entries(SECTOR_STOCKS)) {
        if (symbols.includes(row.symbol)) {
          sector = sectorName
          break
        }
      }
      const status = (PHASE_TO_STATUS[row.phase] || 'WAITING') as any
      map.set(row.symbol, {
        symbol: row.symbol,
        sector,
        status,
        price: row.last_1m_close ?? null,
        changePct: row.pct_change ?? null,
        updatedSec: 0,
        distance: row.distance_pct ?? null,
        timeline: row.last_event ? [row.last_event] : [],
      })
    })
    return map
  }, [rows])

  const filteredStocks = useMemo(() => {
    let symbols: string[] = []
    if (selectedSector === 'All sectors') {
      symbols = Array.from(stockMap.keys())
    } else {
      symbols = SECTOR_STOCKS[selectedSector] || []
    }

    return symbols.filter(symbol => {
      const stock = stockMap.get(symbol)
      if (!stock) return false
      if (search && !symbol.includes(search.toUpperCase())) return false
      if (selectedStatus && stock.status !== selectedStatus) return false
      return true
    }).sort()
  }, [stockMap, selectedSector, search, selectedStatus])

  const armedCount = useMemo(() => rows.filter(r => r.phase?.includes('ARMED')).length, [rows])
  const triggeredCount = useMemo(() => rows.filter(r => r.phase === 'TRIGGERED').length, [rows])
  const rejectedCount = useMemo(() => rows.filter(r => r.phase === 'REJECTED').length, [rows])

  return (
    <div className="flex flex-col h-full bg-background text-on-surface overflow-hidden">
      <div className="flex flex-wrap gap-4 items-center px-4 py-3 bg-surface border-b border-outline shrink-0">
        <select
          value={selectedSector}
          onChange={(e) => setSelectedSector(e.target.value)}
          className="px-3 py-2 border border-outline rounded text-sm"
        >
          {SECTORS.map(sector => (
            <option key={sector} value={sector}>{sector}</option>
          ))}
        </select>

        <select
          value={selectedStatus || ''}
          onChange={(e) => setSelectedStatus(e.target.value || null)}
          className="px-3 py-2 border border-outline rounded text-sm"
        >
          <option value="">All status</option>
          <option value="WAITING">Waiting</option>
          <option value="SPIKE">Spike</option>
          <option value="SETUP_READY">Setup Ready</option>
          <option value="ARMED">Armed</option>
          <option value="TRIGGERED">Triggered</option>
          <option value="REJECTED">Rejected</option>
          <option value="NEGATED">Negated</option>
        </select>

        <div className="flex gap-3 text-sm font-medium">
          <span className="px-2 py-1 bg-green-100 text-green-900 rounded">Armed: {armedCount}</span>
          <span className="px-2 py-1 bg-blue-100 text-blue-900 rounded">Triggered: {triggeredCount}</span>
          <span className="px-2 py-1 bg-red-100 text-red-900 rounded">Rejected: {rejectedCount}</span>
        </div>
      </div>

      <div className="flex-1 overflow-auto custom-scrollbar p-4">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-on-surface-variant">Loading heat map...</p>
          </div>
        ) : (
          <div className="grid gap-2" style={{
            gridTemplateColumns: 'repeat(10, minmax(100px, 1fr))',
            gridAutoRows: 'minmax(100px, auto)',
          }}>
            {filteredStocks.map(symbol => {
              const stock = stockMap.get(symbol)
              if (!stock) return null

              return (
                <button
                  key={symbol}
                  onClick={() => setSelectedStock(stock)}
                  className={`relative p-2 rounded border transition-all hover:border-primary cursor-pointer ${
                    selectedStock?.symbol === symbol ? 'ring-2 ring-primary' : ''
                  }`}
                  style={{
                    backgroundColor: `${STATUS_COLORS[stock.status]}20`,
                    borderColor: STATUS_COLORS[stock.status],
                  }}
                >
                  <div className="text-xs font-bold">{stock.symbol}</div>
                  <div className="text-xs text-on-surface-variant">
                    {stock.price ? `₹${stock.price.toFixed(2)}` : '-'}
                  </div>
                  <div className={`text-xs font-medium ${
                    (stock.changePct || 0) > 0 ? 'text-green-600' :
                    (stock.changePct || 0) < 0 ? 'text-red-600' :
                    'text-on-surface-variant'
                  }`}>
                    {stock.changePct ? `${stock.changePct > 0 ? '+' : ''}${stock.changePct.toFixed(2)}%` : '-'}
                  </div>
                  <div className="text-xs text-on-surface-variant mt-1 px-1 py-0.5 bg-white/50 rounded">
                    {stock.status}
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>

      {selectedStock && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setSelectedStock(null)}>
          <div className="bg-surface rounded-lg p-6 max-w-md max-h-96 overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-lg font-bold">{selectedStock.symbol}</h2>
              <button onClick={() => setSelectedStock(null)} className="text-on-surface-variant hover:text-on-surface">✕</button>
            </div>
            <div className="space-y-2 text-sm">
              <p><strong>Sector:</strong> {selectedStock.sector}</p>
              <p><strong>Status:</strong> <span style={{color: STATUS_COLORS[selectedStock.status]}}>{selectedStock.status}</span></p>
              <p><strong>Price:</strong> ₹{selectedStock.price?.toFixed(2) || '-'}</p>
              <p><strong>Change:</strong> {selectedStock.changePct?.toFixed(2) || '-'}%</p>
              {selectedStock.distance && <p><strong>Distance to Trigger:</strong> {selectedStock.distance.toFixed(2)}%</p>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { fetchAdminAudit } from '../../api/client'
import type { AdminAuditEntry } from '../../api/types'

interface Props {
  currentVersionId: string
  onRollback: (targetVersionId: string) => Promise<void>
}

export function AdminAuditPanel({ currentVersionId, onRollback }: Props) {
  const [entries, setEntries] = useState<AdminAuditEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [rollbackId, setRollbackId] = useState('')
  const [rolling, setRolling] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchAdminAudit(30, 0)
      setEntries(data.entries)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit log')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function handleRollback() {
    if (!rollbackId.trim()) return
    setRolling(true)
    setError(null)
    try {
      await onRollback(rollbackId.trim())
      setRollbackId('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Rollback failed')
    } finally {
      setRolling(false)
    }
  }

  return (
    <section className="border border-outline-variant rounded-lg p-4 bg-surface-container-low">
      <h3 className="text-sm font-semibold text-on-surface mb-2">Audit & rollback</h3>
      <p className="text-[11px] text-on-surface-variant mb-3">
        Active version: <span className="font-data">{currentVersionId}</span>
      </p>
      <div className="flex gap-2 mb-3">
        <input
          type="text"
          value={rollbackId}
          onChange={(e) => setRollbackId(e.target.value)}
          placeholder="Target version_id"
          className="flex-1 border border-outline-variant rounded px-2 py-1 text-xs font-data"
        />
        <button
          type="button"
          onClick={() => void handleRollback()}
          disabled={rolling || !rollbackId.trim()}
          className="px-3 py-1 text-xs border border-outline-variant rounded disabled:opacity-50"
        >
          {rolling ? 'Rolling back…' : 'Rollback'}
        </button>
      </div>
      {error && <p className="text-xs text-red-700 mb-2">{error}</p>}
      {loading ? (
        <p className="text-xs text-on-surface-variant">Loading audit…</p>
      ) : entries.length === 0 ? (
        <p className="text-xs text-on-surface-variant">No audit entries yet.</p>
      ) : (
        <ul className="max-h-48 overflow-auto space-y-1 text-[11px] font-data">
          {entries.map((e) => (
            <li key={e.id} className="border-b border-outline-variant/40 py-1">
              <span className="text-on-surface-variant">{e.at}</span>{' '}
              <span className="font-semibold">{e.action}</span> — {e.result}
              {e.version_id ? ` → ${e.version_id.slice(0, 8)}…` : ''}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

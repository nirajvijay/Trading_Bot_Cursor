import { useCallback, useState, type FormEvent } from 'react'
import { ApiError, postAdminRollback } from '../../api/client'
import { useAdminConfig } from '../../hooks/useAdminConfig'
import { formatVwapPercent } from '../../lib/adminVwapPercent'
import { AdminAuditPanel } from './AdminAuditPanel'
import { AdminStepUpModal } from './AdminStepUpModal'

type PendingAction = 'save' | 'pause' | 'resume' | 'rollback' | null

export function AdminConsolePage() {
  const admin = useAdminConfig(true)
  const [stepUpOpen, setStepUpOpen] = useState(false)
  const [pendingAction, setPendingAction] = useState<PendingAction>(null)
  const [rollbackTargetId, setRollbackTargetId] = useState<string | null>(null)

  const runPending = useCallback(async () => {
    if (pendingAction === 'save') {
      await admin.save()
    } else if (pendingAction === 'pause') {
      await admin.pause()
    } else if (pendingAction === 'resume') {
      await admin.resume()
    } else if (pendingAction === 'rollback' && rollbackTargetId) {
      await postAdminRollback(rollbackTargetId)
      await admin.refresh()
      setRollbackTargetId(null)
    }
    setPendingAction(null)
  }, [admin, pendingAction, rollbackTargetId])

  async function withStepUp(action: PendingAction, fn: () => Promise<void>, extra?: () => void) {
    try {
      await fn()
    } catch (err) {
      if (err instanceof ApiError && err.status === 403 && err.message.toLowerCase().includes('step-up')) {
        extra?.()
        setPendingAction(action)
        setStepUpOpen(true)
        return
      }
      throw err
    }
  }

  const handleRollback = useCallback(
    async (targetVersionId: string) => {
      await withStepUp(
        'rollback',
        async () => {
          await postAdminRollback(targetVersionId)
          await admin.refresh()
        },
        () => setRollbackTargetId(targetVersionId),
      )
    },
    [admin],
  )

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    void withStepUp('save', () => admin.save())
  }

  const paused = admin.config?.entries_paused ?? false
  const engineRunning = admin.config?.engine_running ?? false
  const accepting = admin.config?.accepting_triggers ?? false

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-auto bg-surface-container-lowest">
      <div className="px-4 py-3 border-b border-outline-variant bg-white shrink-0">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-sm font-extrabold uppercase tracking-tight text-on-surface">
            Admin Console
          </h1>
          <span
            className={`label-caps px-2 py-0.5 rounded border text-[10px] ${
              paused
                ? 'bg-amber-50 text-amber-800 border-amber-200'
                : 'bg-emerald-50 text-positive border-emerald-200'
            }`}
          >
            {paused ? 'Entries paused' : 'Accepting new entries'}
          </span>
          <span className="label-caps text-[10px] text-on-surface-variant">
            Engine: {engineRunning ? 'running' : 'stopped'}
          </span>
          <span className="label-caps text-[10px] text-on-surface-variant">
            Triggers: {accepting ? 'on' : 'off'}
          </span>
          {admin.config && (
            <span className="text-[10px] font-data text-on-surface-variant ml-auto">
              v{admin.config.version_id.slice(0, 8)}…
            </span>
          )}
        </div>
        {admin.config?.warnings && admin.config.warnings.length > 0 && (
          <ul className="mt-2 text-[11px] text-amber-800 list-disc pl-4">
            {admin.config.warnings.map((w) => (
              <li key={w}>{w.replaceAll('_', ' ')}</li>
            ))}
          </ul>
        )}
      </div>

      {admin.error && (
        <div className="mx-4 mt-3 px-3 py-2 bg-red-50 border border-red-200 text-red-800 text-xs shrink-0">
          {admin.error}
        </div>
      )}

      <div className="p-4 grid gap-4 lg:grid-cols-2 max-w-5xl">
        <section className="border border-outline-variant rounded-lg p-4 bg-white space-y-4">
          <h2 className="text-sm font-semibold text-on-surface">Risk caps (INR)</h2>
          {admin.form ? (
            <form onSubmit={handleSubmit} className="space-y-3">
              <label className="block text-xs text-on-surface">
                Normal per-trade risk cap (₹)
                <input
                  type="number"
                  min={1}
                  max={2000}
                  step={1}
                  value={admin.form.per_trade_risk_cap_inr}
                  onChange={(e) => admin.updateField('per_trade_risk_cap_inr', e.target.value)}
                  className="mt-1 w-full border border-outline-variant rounded px-2 py-1.5 text-sm font-data"
                  required
                />
              </label>
              <label className="block text-xs text-on-surface">
                LIMITED-VWAP per-trade risk cap (₹)
                <input
                  type="number"
                  min={1}
                  max={2000}
                  step={1}
                  value={admin.form.limited_per_trade_risk_cap_inr}
                  onChange={(e) => admin.updateField('limited_per_trade_risk_cap_inr', e.target.value)}
                  className="mt-1 w-full border border-outline-variant rounded px-2 py-1.5 text-sm font-data"
                  required
                />
              </label>
              <label className="block text-xs text-on-surface">
                Daily loss cap (₹)
                <input
                  type="number"
                  min={1}
                  max={50000}
                  step={1}
                  value={admin.form.daily_loss_cap_inr}
                  onChange={(e) => admin.updateField('daily_loss_cap_inr', e.target.value)}
                  className="mt-1 w-full border border-outline-variant rounded px-2 py-1.5 text-sm font-data"
                  required
                />
                <span className="text-[10px] text-on-surface-variant">
                  Warns if below per-trade cap; does not block save.
                </span>
              </label>

              <h3 className="text-sm font-semibold text-on-surface pt-2">VWAP thresholds (%)</h3>
              <p className="text-[10px] text-on-surface-variant -mt-2">
                Enter as percent (e.g. 0.22 means 0.22%). Active: ACCEPT{' '}
                {admin.config ? formatVwapPercent(admin.config.values.vwap_accept_gap_exclusive_max) : '—'}% / LIMITED{' '}
                {admin.config ? formatVwapPercent(admin.config.values.vwap_limited_gap_inclusive_max) : '—'}%
              </p>
              <label className="block text-xs text-on-surface">
                ACCEPT threshold (%)
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.0001}
                  value={admin.form.vwap_accept_gap_percent}
                  onChange={(e) => admin.updateField('vwap_accept_gap_percent', e.target.value)}
                  className="mt-1 w-full border border-outline-variant rounded px-2 py-1.5 text-sm font-data"
                  required
                />
              </label>
              <label className="block text-xs text-on-surface">
                LIMITED threshold (%)
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.0001}
                  value={admin.form.vwap_limited_gap_percent}
                  onChange={(e) => admin.updateField('vwap_limited_gap_percent', e.target.value)}
                  className="mt-1 w-full border border-outline-variant rounded px-2 py-1.5 text-sm font-data"
                  required
                />
              </label>

              <label className="block text-xs text-on-surface">
                Comment (optional)
                <input
                  type="text"
                  value={admin.form.comment}
                  onChange={(e) => admin.updateField('comment', e.target.value)}
                  className="mt-1 w-full border border-outline-variant rounded px-2 py-1.5 text-sm"
                  maxLength={200}
                />
              </label>

              <div className="flex gap-2 pt-1">
                <button
                  type="submit"
                  disabled={admin.saving || admin.loading}
                  className="px-3 py-1.5 text-xs bg-primary text-white rounded disabled:opacity-50"
                >
                  {admin.saving ? 'Saving…' : 'Save thresholds'}
                </button>
                <button
                  type="button"
                  onClick={admin.resetForm}
                  disabled={admin.saving}
                  className="px-3 py-1.5 text-xs border border-outline-variant rounded"
                >
                  Reset
                </button>
              </div>
            </form>
          ) : (
            <p className="text-xs text-on-surface-variant">{admin.loading ? 'Loading…' : 'No config'}</p>
          )}
        </section>

        <div className="space-y-4">
          <section className="border border-outline-variant rounded-lg p-4 bg-white">
            <h2 className="text-sm font-semibold text-on-surface mb-2">Trading control</h2>
            <p className="text-[11px] text-on-surface-variant mb-3">
              Pause stops new entries and cancels pending VWAP. Open trades continue. State is canonical in admin
              config (survives engine restart).
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={admin.pausing || paused}
                onClick={() => void withStepUp('pause', () => admin.pause())}
                className="px-3 py-1.5 text-xs border border-amber-300 bg-amber-50 text-amber-900 rounded disabled:opacity-50"
              >
                {admin.pausing && paused ? '…' : 'Pause entries'}
              </button>
              <button
                type="button"
                disabled={admin.pausing || !paused}
                onClick={() => void withStepUp('resume', () => admin.resume())}
                className="px-3 py-1.5 text-xs border border-emerald-300 bg-emerald-50 text-emerald-900 rounded disabled:opacity-50"
              >
                {admin.pausing && !paused ? '…' : 'Resume entries'}
              </button>
            </div>
          </section>

          {admin.config && (
            <AdminAuditPanel
              currentVersionId={admin.config.version_id}
              onRollback={handleRollback}
            />
          )}
        </div>
      </div>

      <AdminStepUpModal
        open={stepUpOpen}
        title="Confirm admin action"
        onClose={() => {
          setStepUpOpen(false)
          setPendingAction(null)
          setRollbackTargetId(null)
        }}
        onSuccess={() => {
          void runPending()
        }}
      />
    </div>
  )
}

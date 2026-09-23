import { useMemo, useState } from 'react'
import type { ExecutionPreflight, ExecutionSessionCaps } from '../../api/types'
import { DEFAULT_CAPS } from '../../hooks/useExecutionEngine'
import { inr, lakhs, preconditionLabel } from './format'

interface Props {
  preflight: ExecutionPreflight | null
  busy: boolean
  onStart: (caps: ExecutionSessionCaps, liveOrders: boolean) => Promise<boolean>
}

type Draft = Record<keyof ExecutionSessionCaps, string>

function toDraft(caps: ExecutionSessionCaps): Draft {
  return {
    per_trade_cap_rupees: String(caps.per_trade_cap_rupees),
    per_trade_cap_vwap_limited_rupees: String(caps.per_trade_cap_vwap_limited_rupees),
    daily_loss_cap_rupees: String(caps.daily_loss_cap_rupees),
    total_capital_rupees: String(caps.total_capital_rupees),
    leverage_factor: String(caps.leverage_factor),
  }
}

/** Mirrors engine_config.validate so a bad number is caught before the click,
 *  not after a round trip. The server still enforces it either way. */
function validate(caps: ExecutionSessionCaps): string | null {
  const positive: [string, number][] = [
    ['ACCEPT cap', caps.per_trade_cap_rupees],
    ['LIMITED cap', caps.per_trade_cap_vwap_limited_rupees],
    ['Daily cap', caps.daily_loss_cap_rupees],
    ['Total capital', caps.total_capital_rupees],
  ]
  for (const [name, value] of positive) {
    if (!Number.isFinite(value) || value <= 0) return `${name} must be greater than zero.`
  }
  if (!Number.isFinite(caps.leverage_factor) || caps.leverage_factor < 1) {
    return 'Leverage must be at least 1.'
  }
  if (caps.per_trade_cap_rupees > caps.daily_loss_cap_rupees) {
    return 'ACCEPT cap cannot exceed the daily cap — the daily cap could never bind.'
  }
  if (caps.per_trade_cap_vwap_limited_rupees > caps.daily_loss_cap_rupees) {
    return 'LIMITED cap cannot exceed the daily cap.'
  }
  return null
}

function CapField({
  label,
  hint,
  value,
  onChange,
}: {
  label: string
  hint: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="label-caps text-on-surface-variant">{label}</span>
      <input
        type="number"
        inputMode="decimal"
        className="font-data text-[14px] tabular-nums bg-surface-container-low border border-outline-variant rounded-sm px-2 py-1.5 focus:outline-none focus:border-primary"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <span className="text-[10px] text-on-surface-variant">{hint}</span>
    </label>
  )
}

export function StartPanel({ preflight, busy, onStart }: Props) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(DEFAULT_CAPS))
  const [liveOrders, setLiveOrders] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const caps: ExecutionSessionCaps = useMemo(
    () => ({
      per_trade_cap_rupees: Number(draft.per_trade_cap_rupees),
      per_trade_cap_vwap_limited_rupees: Number(draft.per_trade_cap_vwap_limited_rupees),
      daily_loss_cap_rupees: Number(draft.daily_loss_cap_rupees),
      total_capital_rupees: Number(draft.total_capital_rupees),
      leverage_factor: Number(draft.leverage_factor),
    }),
    [draft],
  )

  const capsError = validate(caps)
  const blocked = preflight ? !preflight.can_start : false
  const canStart = !capsError && !blocked && !busy

  const set = (key: keyof Draft) => (value: string) =>
    setDraft((d) => ({ ...d, [key]: value }))

  return (
    <section className="bg-surface border border-outline-variant rounded-sm">
      <header className="px-4 py-3 border-b border-outline-variant">
        <h2 className="text-[13px] font-extrabold uppercase tracking-tight">
          Start a session
        </h2>
        <p className="text-[11px] text-on-surface-variant mt-0.5">
          These values are read once, at start, and fixed for the whole session. To
          change them, stop and start again.
        </p>
      </header>

      <div className="px-4 py-3 grid grid-cols-2 lg:grid-cols-5 gap-3">
        <CapField
          label="Risk / trade · ACCEPT"
          hint="per-trade price risk"
          value={draft.per_trade_cap_rupees}
          onChange={set('per_trade_cap_rupees')}
        />
        <CapField
          label="Risk / trade · LIMITED"
          hint="smaller cap, same code path"
          value={draft.per_trade_cap_vwap_limited_rupees}
          onChange={set('per_trade_cap_vwap_limited_rupees')}
        />
        <CapField
          label="Daily loss cap"
          hint="realised losses only"
          value={draft.daily_loss_cap_rupees}
          onChange={set('daily_loss_cap_rupees')}
        />
        <CapField
          label="Total capital"
          hint={`buying power ${lakhs(caps.total_capital_rupees * caps.leverage_factor)}`}
          value={draft.total_capital_rupees}
          onChange={set('total_capital_rupees')}
        />
        <CapField
          label="Leverage"
          hint="MIS multiple"
          value={draft.leverage_factor}
          onChange={set('leverage_factor')}
        />
      </div>

      {preflight && (
        <div className="px-4 pb-3">
          <p className="label-caps text-on-surface-variant mb-1.5">Preconditions</p>
          <ul className="grid sm:grid-cols-2 gap-x-4 gap-y-1">
            {preflight.checks.map((check) => (
              <li key={check.key} className="flex items-start gap-2 text-[12px]">
                <span
                  className={`mt-[3px] size-1.5 rounded-full shrink-0 ${
                    check.ok ? 'bg-positive' : 'bg-negative'
                  }`}
                />
                <span className={check.ok ? 'text-on-surface' : 'text-negative'}>
                  {preconditionLabel(check.key)}
                  {!check.ok && (
                    <span className="text-on-surface-variant"> — {check.detail}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {capsError && (
        <p className="mx-4 mb-3 px-3 py-2 bg-red-50 border border-red-200 rounded-sm text-[12px] text-negative">
          {capsError}
        </p>
      )}

      <footer className="px-4 py-3 border-t border-outline-variant flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-[12px]">
          <input
            type="checkbox"
            checked={liveOrders}
            onChange={(e) => setLiveOrders(e.target.checked)}
          />
          <span className={liveOrders ? 'text-negative font-semibold' : ''}>
            Place real Kite orders
          </span>
        </label>
        <button
          type="button"
          disabled={!canStart}
          onClick={() => setConfirming(true)}
          className="label-caps px-4 py-2 rounded-sm bg-primary text-white disabled:bg-surface-container disabled:text-on-surface-variant"
        >
          {busy ? 'Starting…' : 'Start engine'}
        </button>
        <p className="text-[11px] text-on-surface-variant">
          Start means armed. The first qualifying trigger is entered for real — there is
          no rehearsal mode.
        </p>
      </footer>

      {confirming && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-surface border border-outline-variant rounded-sm max-w-md w-full shadow-lg">
            <h3 className="px-4 py-3 border-b border-outline-variant text-[13px] font-extrabold uppercase tracking-tight">
              Confirm session limits
            </h3>
            <dl className="px-4 py-3 space-y-1.5 text-[12px]">
              {[
                ['Risk per trade · ACCEPT', inr(caps.per_trade_cap_rupees)],
                ['Risk per trade · LIMITED', inr(caps.per_trade_cap_vwap_limited_rupees)],
                ['Daily loss cap', inr(caps.daily_loss_cap_rupees)],
                ['Total capital', inr(caps.total_capital_rupees, 0)],
                [
                  'Buying power',
                  lakhs(caps.total_capital_rupees * caps.leverage_factor),
                ],
                ['Orders', liveOrders ? 'REAL Kite orders' : 'Internal broker only'],
              ].map(([label, value]) => (
                <div key={label} className="flex justify-between gap-4">
                  <dt className="text-on-surface-variant">{label}</dt>
                  <dd
                    className={`font-data tabular-nums ${
                      label === 'Orders' && liveOrders
                        ? 'text-negative font-semibold'
                        : ''
                    }`}
                  >
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
            <div className="px-4 py-3 border-t border-outline-variant flex justify-end gap-2">
              <button
                type="button"
                className="label-caps px-3 py-2 rounded-sm border border-outline-variant"
                onClick={() => setConfirming(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="label-caps px-3 py-2 rounded-sm bg-primary text-white"
                onClick={async () => {
                  setConfirming(false)
                  await onStart(caps, liveOrders)
                }}
              >
                Start with these limits
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

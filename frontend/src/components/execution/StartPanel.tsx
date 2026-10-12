import { useEffect, useMemo, useState } from 'react'
import type {
  ArbStage,
  ExecutionPreflight,
  ExecutionSessionCaps,
  ExecutionSettings,
  StrategyMode,
} from '../../api/types'
import { DEFAULT_CAPS, type StartChoice } from '../../hooks/useExecutionEngine'
import { inr, lakhs, preconditionLabel, stageLabel } from './format'

interface Props {
  preflight: ExecutionPreflight | null
  busy: boolean
  onStart: (
    caps: ExecutionSessionCaps,
    liveOrders: boolean,
    choice?: StartChoice,
  ) => Promise<boolean>
  /** The ARB stages and what autostart launches. Null until loaded. */
  settings: ExecutionSettings | null
  savingSettings: boolean
  onSaveAutostart: (
    strategy: StrategyMode,
    arbRiskScale: number,
    arbCapital: number,
  ) => Promise<boolean>
}

const ARB_DEFAULT_CAPITAL = 500000

/** What ARB trades at on one stage, as the rows of the confirm dialog. */
function stageRows(stage: ArbStage, keep: number): [string, string][] {
  return [
    ['Risk per ORB trade', inr(stage.orb_risk, 0)],
    [
      'Risk per funnel trade, score 0 / 1 / 2 / 3',
      stage.funnel_risks.map((r) => inr(r, 0)).join(' / '),
    ],
    ['Open-risk cap (plus the day\'s profit)', inr(stage.open_risk_cap, 0)],
    ['Smallest trade taken', inr(stage.min_risk, 0)],
    ['Hard day stop (day P&L after charges)', `−${inr(stage.hard_day_stop, 0)}`],
    [
      'Day lock',
      `arms at ${inr(stage.day_lock_arm, 0)}, keeps ${Math.round(keep * 100)}% of the peak`,
    ],
    ['Closed-loss backstop', inr(stage.closed_loss_backstop, 0)],
  ]
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
  value,
  onChange,
  wide,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  wide?: boolean
}) {
  return (
    <label
      className={`flex flex-col gap-1 min-w-0 ${wide ? 'flex-[1_1_140px]' : 'flex-[1_1_110px]'}`}
    >
      <span className="label-caps text-on-surface-variant whitespace-nowrap">{label}</span>
      <span className="flex items-center bg-surface border border-[#d6dbe4] rounded focus-within:border-primary">
        <span className="pl-2 font-data text-[12px] text-on-surface-variant">₹</span>
        <input
          type="number"
          inputMode="decimal"
          className="flex-1 min-w-0 w-full font-data text-[13px] font-semibold tabular-nums bg-transparent border-0 py-1.5 pl-[3px] pr-2 focus:outline-none"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </span>
    </label>
  )
}

/** A hover card anchored to its trigger. Pure CSS, so it never lags a poll. */
function Hover({
  trigger,
  children,
  className,
}: {
  trigger: React.ReactNode
  children: React.ReactNode
  className: string
}) {
  return (
    <span className="relative inline-flex group">
      {trigger}
      <span className={`absolute z-30 hidden group-hover:flex group-focus-within:flex ${className}`}>
        {children}
      </span>
    </span>
  )
}

export function StartPanel({
  preflight,
  busy,
  onStart,
  settings,
  savingSettings,
  onSaveAutostart,
}: Props) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(DEFAULT_CAPS))
  const [liveOrders, setLiveOrders] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [mode, setMode] = useState<StrategyMode>('legacy_vwap')
  const [arbCapital, setArbCapital] = useState(String(ARB_DEFAULT_CAPITAL))
  const [arbScale, setArbScale] = useState<number | null>(null)

  // Open on whatever autostart is set to, once, when the settings arrive.
  const loaded = settings !== null
  useEffect(() => {
    if (!settings) return
    setMode(settings.autostart_strategy)
    setArbCapital(String(settings.arb_total_capital_rupees))
    setArbScale(settings.arb_risk_scale)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded])

  const arb = mode === 'arb'
  const stages = settings?.stages ?? []
  const stage =
    stages.find((s) => s.risk_scale === arbScale && s.unlocked) ??
    stages.find((s) => s.unlocked) ??
    null

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

  const arbCaps: ExecutionSessionCaps = useMemo(
    () => ({ ...DEFAULT_CAPS, total_capital_rupees: Number(arbCapital) }),
    [arbCapital],
  )
  const arbError = !stage
    ? 'The ARB stages have not loaded yet.'
    : !Number.isFinite(arbCaps.total_capital_rupees) || arbCaps.total_capital_rupees <= 0
      ? 'Total capital must be greater than zero.'
      : null
  const capsError = arb ? arbError : validate(caps)
  const blocked = preflight ? !preflight.can_start : false
  const canStart = !capsError && !blocked && !busy
  const autostartDirty =
    !!settings &&
    (settings.autostart_strategy !== mode ||
      (arb &&
        (settings.arb_total_capital_rupees !== arbCaps.total_capital_rupees ||
          settings.arb_risk_scale !== (stage?.risk_scale ?? settings.arb_risk_scale))))
  const checks = preflight?.checks ?? []
  const passed = checks.filter((c) => c.ok).length
  const allOk = preflight ? passed === checks.length : false

  const set = (key: keyof Draft) => (value: string) =>
    setDraft((d) => ({ ...d, [key]: value }))

  return (
    <section className="bg-surface border border-outline-variant rounded-md px-4 py-3 flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <h2 className="text-[13px] font-extrabold uppercase tracking-tight">Start a session</h2>
        <span className="text-[11px] text-on-surface-variant">
          Read once at start and fixed for the session.
        </span>
        <span className="inline-flex rounded border border-outline-variant overflow-hidden">
          {(
            [
              ['legacy_vwap', 'Legacy VWAP'],
              ['arb', 'ARB'],
            ] as [StrategyMode, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setMode(value)}
              className={`label-caps px-2.5 py-[5px] ${
                mode === value
                  ? 'bg-primary text-white'
                  : 'bg-surface text-on-surface-variant hover:bg-surface-container-low'
              }`}
            >
              {label}
            </button>
          ))}
        </span>

        {preflight && (
          <span className="ml-auto">
            <Hover
              className="top-[calc(100%+6px)] right-0 w-[280px] flex-col overflow-hidden bg-surface border border-outline-variant rounded-md shadow-[0_10px_24px_rgba(11,28,48,0.14)]"
              trigger={
                <span
                  tabIndex={0}
                  className={`inline-flex items-center gap-1.5 label-caps px-2 py-[3px] rounded border cursor-help ${
                    allOk
                      ? 'bg-emerald-50 text-positive border-emerald-200'
                      : 'bg-red-50 text-negative border-red-200'
                  }`}
                >
                  <span className={`size-1.5 rounded-full ${allOk ? 'bg-positive' : 'bg-negative'}`} />
                  Preconditions {passed}/{checks.length}
                </span>
              }
            >
              <span className="px-3 py-2 border-b border-outline-variant label-caps text-on-surface-variant">
                Checked before start
              </span>
              {checks.map((check) => (
                <span
                  key={check.key}
                  className="flex items-center gap-[9px] px-3 py-[7px] border-b border-[#eef0f4] last:border-b-0 text-[12px]"
                >
                  <span
                    className={`shrink-0 size-[15px] rounded-full text-white flex items-center justify-center text-[9px] font-extrabold ${
                      check.ok ? 'bg-positive' : 'bg-negative'
                    }`}
                  >
                    {check.ok ? '✓' : '✕'}
                  </span>
                  <span className={check.ok ? '' : 'text-negative'}>
                    {preconditionLabel(check.key)}
                    {!check.ok && (
                      <span className="text-on-surface-variant"> — {check.detail}</span>
                    )}
                  </span>
                </span>
              ))}
            </Hover>
          </span>
        )}
      </div>

      {arb && (
        <div className="flex flex-wrap items-end gap-x-3 gap-y-2.5">
          <div className="flex flex-col gap-1">
            <span className="label-caps text-on-surface-variant">Stage</span>
            <span className="inline-flex rounded border border-outline-variant overflow-hidden">
              {stages.map((s) => (
                <button
                  key={s.key}
                  type="button"
                  disabled={!s.unlocked}
                  title={
                    s.unlocked
                      ? undefined
                      : 'Locked until the promotion gates on the Performance tab pass'
                  }
                  onClick={() => setArbScale(s.risk_scale)}
                  className={`label-caps px-2.5 py-[7px] ${
                    stage?.key === s.key
                      ? 'bg-primary text-white'
                      : 'bg-surface text-on-surface-variant'
                  } disabled:bg-surface-container disabled:text-[#a3a8b3] disabled:cursor-not-allowed`}
                >
                  {stageLabel(s.risk_scale)}
                  {!s.unlocked && ' 🔒'}
                </button>
              ))}
            </span>
          </div>
          <CapField label="Total capital" value={arbCapital} onChange={setArbCapital} wide />
          <div className="flex flex-col gap-1 shrink-0">
            <span className="label-caps text-on-surface-variant">Buying power</span>
            <span className="font-data text-[13px] py-1.5 whitespace-nowrap">
              <span className="font-bold text-primary">
                {lakhs(arbCaps.total_capital_rupees * arbCaps.leverage_factor)}
              </span>
              <span className="text-on-surface-variant"> · {arbCaps.leverage_factor}x</span>
            </span>
          </div>
          <span className="label-caps text-negative py-[9px]">Real Kite orders</span>
          <button
            type="button"
            disabled={!canStart}
            onClick={() => setConfirming(true)}
            className="shrink-0 label-caps px-[18px] py-[9px] rounded bg-primary text-white disabled:bg-surface-container disabled:text-on-surface-variant disabled:cursor-not-allowed"
          >
            {busy ? 'Starting…' : 'Start ARB'}
          </button>
        </div>
      )}
      {arb && stage && (
        <p className="text-[11px] text-on-surface-variant text-pretty">
          ORB and the funnel through one risk budget. At {stageLabel(stage.risk_scale)}: ORB{' '}
          {inr(stage.orb_risk, 0)} a trade, funnel{' '}
          {stage.funnel_risks.map((r) => inr(r, 0)).join(' / ')} by score, open-risk cap{' '}
          {inr(stage.open_risk_cap, 0)}, hard day stop −{inr(stage.hard_day_stop, 0)}. There is
          no paper ARB: every order is a real Kite order at this size.
        </p>
      )}

      {!arb && (
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2.5">
        <CapField
          label="Risk · ACCEPT"
          value={draft.per_trade_cap_rupees}
          onChange={set('per_trade_cap_rupees')}
        />
        <CapField
          label="Risk · LIMITED"
          value={draft.per_trade_cap_vwap_limited_rupees}
          onChange={set('per_trade_cap_vwap_limited_rupees')}
        />
        <CapField
          label="Daily loss cap"
          value={draft.daily_loss_cap_rupees}
          onChange={set('daily_loss_cap_rupees')}
        />
        <CapField
          label="Total capital"
          value={draft.total_capital_rupees}
          onChange={set('total_capital_rupees')}
          wide
        />
        <div className="flex flex-col gap-1 shrink-0">
          <span className="label-caps text-on-surface-variant">Buying power</span>
          <span className="font-data text-[13px] py-1.5 whitespace-nowrap">
            <span className="font-bold text-primary">
              {lakhs(caps.total_capital_rupees * caps.leverage_factor)}
            </span>
            <span className="text-on-surface-variant"> · {caps.leverage_factor}x</span>
          </span>
        </div>

        <Hover
          className="bottom-[calc(100%+6px)] left-1/2 -translate-x-1/2 w-[220px] flex-col gap-[3px] whitespace-normal bg-on-surface text-white rounded-md px-2.5 py-2 shadow-[0_8px_20px_rgba(11,28,48,0.25)]"
          trigger={
            <label className="flex items-center gap-[5px] px-0.5 py-[7px] cursor-pointer whitespace-nowrap">
              <input
                type="checkbox"
                checked={liveOrders}
                onChange={(e) => setLiveOrders(e.target.checked)}
                className="size-[15px] m-0 accent-negative cursor-pointer"
              />
              <span className={`label-caps ${liveOrders ? 'text-negative' : ''}`}>Live</span>
            </label>
          }
        >
          <span className="label-caps text-red-200">Place real Kite orders</span>
          <span className="text-[11px] leading-snug">
            {liveOrders
              ? 'On — the engine will send real orders to Kite.'
              : 'Off — orders go to the internal broker only.'}
          </span>
        </Hover>

        <button
          type="button"
          disabled={!canStart}
          onClick={() => setConfirming(true)}
          className="shrink-0 label-caps px-[18px] py-[9px] rounded bg-primary text-white disabled:bg-surface-container disabled:text-on-surface-variant disabled:cursor-not-allowed"
        >
          {busy ? 'Starting…' : 'Start engine'}
        </button>
      </div>
      )}

      {settings && (
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 pt-2 border-t border-[#eef0f4] text-[11px]">
          <span className="label-caps text-on-surface-variant">09:15 autostart</span>
          <span>
            launches{' '}
            <span className="font-semibold">
              {settings.autostart_strategy === 'arb'
                ? `ARB at ${stageLabel(settings.arb_risk_scale)} on ${lakhs(settings.arb_total_capital_rupees)}`
                : 'Legacy VWAP with the default caps'}
            </span>
            , with real Kite orders.
          </span>
          {autostartDirty && !capsError && (
            <button
              type="button"
              disabled={savingSettings}
              onClick={() =>
                void onSaveAutostart(
                  mode,
                  stage?.risk_scale ?? settings.arb_risk_scale,
                  arb ? arbCaps.total_capital_rupees : settings.arb_total_capital_rupees,
                )
              }
              className="label-caps px-2 py-[3px] rounded border border-outline-variant bg-surface hover:bg-surface-container-low disabled:opacity-50"
            >
              {savingSettings
                ? 'Saving…'
                : `Autostart ${arb ? `ARB at ${stageLabel(stage?.risk_scale ?? settings.arb_risk_scale)}` : 'Legacy VWAP'} instead`}
            </button>
          )}
          {settings.autostart_strategy === 'arb' && (
            <span className="text-on-surface-variant">
              ORB breakouts start at 09:20, so an ARB day needs the engine up before then.
            </span>
          )}
        </div>
      )}

      {capsError && (
        <p className="px-2.5 py-1.5 bg-red-50 border border-red-200 rounded text-[12px] text-negative">
          {capsError}
        </p>
      )}

      {confirming && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-surface border border-outline-variant rounded-md max-w-md w-full shadow-lg">
            <h3 className="px-4 py-[13px] border-b border-outline-variant text-[13px] font-extrabold uppercase tracking-tight">
              {arb && stage ? `Confirm ARB at ${stageLabel(stage.risk_scale)}` : 'Confirm session limits'}
            </h3>
            <dl className="px-4 py-3 flex flex-col gap-[7px] text-[12px]">
              {(arb && stage
                ? [
                    ...stageRows(stage, settings?.day_lock_keep ?? 0.4),
                    ['Total capital', inr(arbCaps.total_capital_rupees, 0)],
                    [
                      'Buying power',
                      lakhs(arbCaps.total_capital_rupees * arbCaps.leverage_factor),
                    ],
                    ['Orders', 'REAL Kite orders'],
                  ]
                : [
                    ['Risk per trade · ACCEPT', inr(caps.per_trade_cap_rupees)],
                    ['Risk per trade · LIMITED', inr(caps.per_trade_cap_vwap_limited_rupees)],
                    ['Daily loss cap', inr(caps.daily_loss_cap_rupees)],
                    ['Total capital', inr(caps.total_capital_rupees, 0)],
                    [
                      'Buying power',
                      lakhs(caps.total_capital_rupees * caps.leverage_factor),
                    ],
                    ['Orders', liveOrders ? 'REAL Kite orders' : 'Internal broker only'],
                  ]
              ).map(([label, value]) => (
                <div key={label} className="flex justify-between gap-4">
                  <dt className="text-on-surface-variant">{label}</dt>
                  <dd
                    className={`font-data tabular-nums text-right ${
                      label === 'Orders' && (liveOrders || arb)
                        ? 'text-negative font-bold'
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
                className="label-caps px-3 py-[9px] rounded border border-outline-variant bg-surface"
                onClick={() => setConfirming(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="label-caps px-3 py-[9px] rounded bg-primary text-white"
                onClick={async () => {
                  setConfirming(false)
                  if (arb && stage) {
                    await onStart(arbCaps, true, {
                      strategyMode: 'arb',
                      arbRiskScale: stage.risk_scale,
                    })
                  } else {
                    await onStart(caps, liveOrders)
                  }
                }}
              >
                {arb ? 'Start ARB with real orders' : 'Start with these limits'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

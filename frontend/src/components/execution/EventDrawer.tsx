import { useEffect, useState } from 'react'
import { fetchExecutionEvents } from '../../api/client'
import type { ExecutionEvent, ExecutionPosition } from '../../api/types'
import {
  inr,
  isUnprotected,
  num,
  pnlClass,
  reasonLabel,
  stateLabel,
  timeIst,
} from './format'
import { Side, Tier, type PositionKind } from './PositionTables'

const EVENT_LABELS: Record<string, string> = {
  entry_intent: 'Intent written before the broker call',
  entry_submitted: 'Market order sent',
  entry_ambiguous: 'Broker response was ambiguous',
  entry_unreached: 'Order never reached the broker',
  entry_filled: 'Filled',
  entry_rejected: 'Broker rejected the entry',
  protected: 'Protective stop live',
  stop_accepted_visibility_unknown: 'Stop accepted, not yet visible',
  stop_place_failed: 'Stop placement failed',
  stop_missing_at_broker: 'Stop vanished at the broker',
  stop_cancelled: 'Stop cancelled',
  stop_cancel_ambiguous: 'Stop cancellation unconfirmed',
  reconciled: 'Adopted broker truth',
  exit_submitted: 'Exit order sent',
  flatten_failed: 'Flatten failed',
  closed: 'Closed',
  cancelled: 'Cancelled',
  skipped: 'Skipped',
  entry_cancel_raced_fill: 'Cancel raced a fill',
  stop_move_failed: 'Stop move failed — stop left where it was',
  stop_replaced_for_mod_cap: 'Stop re-placed (Kite modification limit)',
}

const SOURCE_LABELS: Record<string, string> = {
  auto: 'auto-trail',
  manual: 'manual nudge',
  kite: 'changed in Kite',
}

/** Stop changes name who made them: auto-trail, a manual nudge, or Kite. */
function eventLabel(event: ExecutionEvent): string {
  const source = String(event.payload.source ?? '')
  const who = SOURCE_LABELS[source] ?? source
  if (event.event_type === 'stop_moved') {
    return source === 'manual' ? `Stop nudged · ${who}` : `Stop trailed · ${who}`
  }
  if (event.event_type === 'stop_adopted') {
    return source === 'kite'
      ? 'Stop changed directly in Kite · adopted'
      : `Stop move confirmed late · ${who}`
  }
  if (event.event_type === 'trail_toggled') {
    return event.payload.to ? 'Auto-trail switched on' : 'Auto-trail switched off'
  }
  return EVENT_LABELS[event.event_type] ?? event.event_type
}

function isAlarming(type: string): boolean {
  return (
    type.includes('failed') ||
    type.includes('ambiguous') ||
    type.includes('rejected') ||
    type === 'stop_missing_at_broker'
  )
}

const GOOD = new Set(['entry_filled', 'protected', 'closed'])

type Tone = 'live' | 'alarm' | 'warn' | 'idle'

const VERDICT_TONES: Record<Tone, { box: string; fg: string; dot: string; glyph: string }> = {
  live: { box: 'bg-emerald-50 border-emerald-200', fg: 'text-positive', dot: 'bg-positive', glyph: '✓' },
  alarm: { box: 'bg-red-100 border-negative', fg: 'text-negative', dot: 'bg-negative', glyph: '!' },
  warn: { box: 'bg-amber-50 border-amber-200', fg: 'text-amber-800', dot: 'bg-amber-800', glyph: '–' },
  idle: { box: 'bg-surface border-outline-variant', fg: 'text-on-surface-variant', dot: 'bg-on-surface-variant', glyph: '■' },
}

/** One-line answer to "what happened to this trade?", read off the diary. */
function verdict(
  row: ExecutionPosition,
  kind: PositionKind,
  events: ExecutionEvent[],
): { tone: Tone; title: string; sub: string } {
  const find = (type: string) => events.find((e) => e.event_type === type)
  if (kind === 'skipped') {
    return {
      tone: 'warn',
      title: `Skipped · ${reasonLabel(row.skip_reason)}`,
      sub: `The engine passed on this trigger at ${timeIst(row.created_at)}. State: ${stateLabel(row.state)}.`,
    }
  }
  if (kind === 'closed') {
    const mismatch = row.pnl_mismatch
      ? ` Kite day P&L differs from our trades by ${inr(Math.abs(row.pnl_mismatch.diff))}.`
      : ''
    return {
      tone: (row.realised_pnl ?? 0) < 0 ? 'idle' : 'live',
      title: `Closed · ${reasonLabel(row.close_reason)} · ${
        row.realised_unattributed ? 'unattributed' : inr(row.realised_pnl)
      }`,
      sub: `Position is flat since ${timeIst(row.updated_at)}.${mismatch}`,
    }
  }
  if (isUnprotected(row.state)) {
    const failed = events.filter((e) => e.event_type === 'stop_place_failed').length
    return {
      tone: 'alarm',
      title: 'Filled with no confirmed stop',
      sub: failed
        ? `${failed} stop placement attempt${failed === 1 ? '' : 's'} failed. The engine retries every tick; new entries are held back.`
        : 'The engine retries protection every tick; new entries are held back.',
    }
  }
  const protectedAt = find('protected')
  if (protectedAt) {
    return {
      tone: 'live',
      title: `Protected · stop live at ${num(row.stop_price)}`,
      sub: `Structural stop confirmed at the broker since ${timeIst(protectedAt.at)}.${
        row.stop_adopted_from_broker ? ' Stop was later adopted from a change made in Kite.' : ''
      }`,
    }
  }
  return { tone: 'idle', title: stateLabel(row.state), sub: 'In flight — waiting on the broker.' }
}

type StageState = 'done' | 'warn' | 'fail' | 'next' | 'todo'

const STAGE_STYLES: Record<StageState, { dot: string; label: string; glyph: string }> = {
  done: { dot: 'bg-positive border-positive text-white', label: 'text-on-surface', glyph: '✓' },
  warn: { dot: 'bg-amber-500 border-amber-500 text-white', label: 'text-amber-800', glyph: '–' },
  fail: { dot: 'bg-negative border-negative text-white', label: 'text-negative', glyph: '✕' },
  next: { dot: 'bg-surface border-primary text-primary', label: 'text-primary', glyph: '' },
  todo: { dot: 'bg-surface border-[#c4c7cf] text-[#c4c7cf]', label: 'text-on-surface-variant', glyph: '' },
}

function Lifecycle({ kind, events }: { kind: PositionKind; events: ExecutionEvent[] }) {
  const find = (type: string) => events.find((e) => e.event_type === type)
  const stopFailed = events.some((e) => e.event_type === 'stop_place_failed')
  const defs: [string, string][] =
    kind === 'skipped'
      ? [['Intent', 'entry_intent'], ['Skipped', 'skipped']]
      : [
          ['Intent', 'entry_intent'],
          ['Sent', 'entry_submitted'],
          ['Filled', 'entry_filled'],
          ['Protected', 'protected'],
          ['Closed', 'closed'],
        ]
  let nextMarked = false
  const stages = defs.map(([label, type]) => {
    const event = find(type)
    let state: StageState = event ? 'done' : 'todo'
    if (type === 'skipped' && event) state = 'warn'
    if (type === 'protected' && !event && stopFailed) state = 'fail'
    if (state === 'todo' && !nextMarked) {
      nextMarked = true
      state = 'next'
    }
    return { label, type, event, state }
  })

  return (
    <section className="shrink-0 bg-surface border border-outline-variant rounded-md px-2 pt-3 pb-2.5">
      <p className="mx-2 mb-3 label-caps text-on-surface-variant">Lifecycle</p>
      <div className="flex overflow-hidden">
        {stages.map((stage, i) => {
          const style = STAGE_STYLES[stage.state]
          const last = i === stages.length - 1
          const nextDone = !last && !!stages[i + 1].event
          return (
            <div key={stage.type} className="flex-1 min-w-0 relative flex flex-col items-center gap-[5px]">
              {!last && (
                <span
                  className={`absolute top-[11px] left-1/2 w-full h-0.5 ${
                    nextDone ? 'bg-positive' : 'bg-outline-variant'
                  }`}
                />
              )}
              <span
                className={`relative z-[1] size-6 rounded-full border-2 flex items-center justify-center text-[12px] font-extrabold ${style.dot}`}
              >
                {style.glyph}
              </span>
              <span className={`label-caps text-center ${style.label}`}>{stage.label}</span>
              <span className="font-data text-[10px] text-on-surface-variant">
                {stage.event ? timeIst(stage.event.at) : stage.state === 'fail' ? 'failing' : '—'}
              </span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function facts(row: ExecutionPosition, kind: PositionKind): { k: string; v: string; tone?: string }[] {
  if (kind === 'skipped') {
    return [
      { k: 'Tier', v: row.vwap_classification ?? '—' },
      { k: 'Reason', v: reasonLabel(row.skip_reason) },
      { k: 'State', v: stateLabel(row.state), tone: 'text-on-surface-variant' },
      { k: 'At', v: timeIst(row.created_at) },
    ]
  }
  const base = [
    { k: 'Qty', v: String(row.qty) },
    { k: 'Entry', v: num(row.entry_price) },
  ]
  if (kind === 'closed') {
    return [
      ...base,
      { k: 'Closed by', v: reasonLabel(row.close_reason) },
      {
        k: 'Realised P&L',
        v: row.realised_unattributed ? 'unattributed' : inr(row.realised_pnl),
        tone: row.realised_unattributed ? 'text-negative' : pnlClass(row.realised_pnl),
      },
    ]
  }
  return [
    ...base,
    { k: 'Stop', v: num(row.stop_price) },
    { k: 'Ongoing P&L', v: inr(row.live_pnl), tone: pnlClass(row.live_pnl) },
  ]
}

function deltaLabel(seconds: number): string {
  if (seconds <= 0) return 'T+0s'
  if (seconds < 60) return `T+${Math.round(seconds)}s`
  return `T+${Math.floor(seconds / 60)}m`
}

function payloadText(value: unknown): string {
  if (value === null || value === undefined) return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function EventItem({ event, startMs }: { event: ExecutionEvent; startMs: number }) {
  const bad = isAlarming(event.event_type)
  const good = GOOD.has(event.event_type)
  const entries = Object.entries(event.payload).map(([k, v]) => ({ k, v: payloadText(v) }))
  // A long value (a broker's rejection message) gets the full width and wraps.
  // Truncating it would hide the one thing worth reading.
  const chips = entries.filter((e) => e.v.length <= 24)
  const notes = entries.filter((e) => e.v.length > 24)
  const at = Date.parse(event.at)

  return (
    <li className="grid grid-cols-[62px_18px_minmax(0,1fr)] gap-x-2">
      <div className="pt-[9px] flex flex-col items-end gap-0.5">
        <span className="font-data text-[11px] font-semibold">{timeIst(event.at)}</span>
        <span className="font-data text-[10px] text-on-surface-variant">
          {Number.isNaN(at) ? '' : deltaLabel((at - startMs) / 1000)}
        </span>
      </div>
      <div className="relative flex justify-center">
        <span className="absolute inset-y-0 w-0.5 bg-outline-variant" />
        <span
          className={`relative mt-3 size-2.5 rounded-full shadow-[0_0_0_3px_#f8f9ff] ${
            bad
              ? 'bg-negative'
              : good
                ? 'bg-positive'
                : event.event_type === 'skipped'
                  ? 'bg-amber-500'
                  : 'bg-primary'
          }`}
        />
      </div>
      <div className="pt-1 pb-2">
        <div
          className={`border rounded-md px-[11px] py-2 flex flex-col gap-1.5 ${
            bad ? 'bg-red-50 border-red-200' : 'bg-surface border-outline-variant'
          }`}
        >
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className={`text-[12px] font-bold ${bad ? 'text-negative' : ''}`}>
              {eventLabel(event)}
            </span>
            <span className="ml-auto font-data text-[10px] text-on-surface-variant">
              {event.event_type}
            </span>
          </div>
          {chips.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {chips.map(({ k, v }) => (
                <span
                  key={k}
                  className="inline-flex items-baseline gap-[5px] px-1.5 py-0.5 bg-surface border border-outline-variant rounded-[3px]"
                >
                  <span className="text-[10px] text-on-surface-variant">{k}</span>
                  <span className="font-data text-[10px] font-semibold">{v}</span>
                </span>
              ))}
            </div>
          )}
          {notes.map(({ k, v }) => (
            <div
              key={k}
              className={`bg-surface border rounded px-2 py-1.5 flex flex-col gap-0.5 ${
                bad ? 'border-red-200' : 'border-outline-variant'
              }`}
            >
              <span className="text-[10px] text-on-surface-variant">{k}</span>
              <span
                className={`font-data text-[11px] [overflow-wrap:anywhere] ${bad ? 'text-negative' : ''}`}
              >
                {v}
              </span>
            </div>
          ))}
        </div>
      </div>
    </li>
  )
}

/** The append-only diary for one trade.
 *
 *  This is the review surface: after a small live trade, pull this up and read
 *  it against what Kite's own order history shows for the same trade. */
export function EventDrawer({
  row,
  kind,
  onClose,
}: {
  row: ExecutionPosition
  kind: PositionKind
  onClose: () => void
}) {
  const tradeId = row.trade_id
  const [events, setEvents] = useState<ExecutionEvent[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setEvents(null)
    setError(null)
    fetchExecutionEvents(tradeId)
      .then((data) => {
        if (!cancelled) setEvents(data.events)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load')
      })
    return () => {
      cancelled = true
    }
  }, [tradeId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const list = events ?? []
  const alerts = list.filter((e) => isAlarming(e.event_type)).length
  const v = verdict(row, kind, list)
  const tone = VERDICT_TONES[v.tone]
  const startMs = list.length ? Date.parse(list[0].at) : 0

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-[rgba(11,28,48,0.28)]"
      onClick={onClose}
    >
      <aside
        className="bg-background w-full max-w-[600px] h-full flex flex-col border-l border-outline-variant shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="bg-surface px-[18px] py-3.5 border-b border-outline-variant flex items-start gap-3">
          <div className="min-w-0 flex flex-col gap-[5px]">
            <p className="label-caps text-on-surface-variant">Event diary</p>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-data text-[18px] font-bold tracking-tight">
                {row.tradingsymbol}
              </span>
              {kind !== 'skipped' && <Side direction={row.direction} />}
              <Tier tier={row.vwap_classification} />
            </div>
            <p className="font-data text-[11px] text-on-surface-variant truncate">{tradeId}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto label-caps px-2.5 py-1.5 rounded border border-outline-variant bg-surface hover:bg-surface-container-low whitespace-nowrap"
          >
            Close · Esc
          </button>
        </header>

        <div className="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar px-[18px] py-4 flex flex-col gap-3.5">
          {error && (
            <p className="px-3 py-2 bg-red-50 border border-red-200 rounded text-[12px] text-negative">
              {error}
            </p>
          )}

          {events !== null && (
            <>
              <section className={`shrink-0 border rounded-md px-3.5 py-3 flex items-start gap-3 ${tone.box}`}>
                <span
                  className={`shrink-0 size-[26px] rounded-full text-white flex items-center justify-center text-[13px] font-extrabold ${tone.dot}`}
                >
                  {tone.glyph}
                </span>
                <div className="min-w-0 flex flex-col gap-[3px]">
                  <p className={`text-[14px] font-extrabold ${tone.fg}`}>{v.title}</p>
                  <p className="text-[12px] text-pretty">{v.sub}</p>
                </div>
              </section>

              <Lifecycle kind={kind} events={list} />
            </>
          )}

          <section className="shrink-0 grid grid-cols-[repeat(auto-fit,minmax(110px,1fr))] gap-px bg-outline-variant border border-outline-variant rounded-md overflow-hidden">
            {facts(row, kind).map((f) => (
              <div key={f.k} className="bg-surface px-3 py-[9px] flex flex-col gap-[3px]">
                <span className="label-caps text-on-surface-variant">{f.k}</span>
                <span
                  className={`font-data text-[13px] font-semibold tabular-nums [overflow-wrap:anywhere] ${f.tone ?? ''}`}
                >
                  {f.v}
                </span>
              </div>
            ))}
          </section>

          <section className="shrink-0 flex flex-col gap-2.5">
            <div className="flex items-center gap-2">
              <p className="label-caps text-on-surface-variant">Events</p>
              <span className="font-data text-[11px] text-on-surface-variant">
                {events === null ? '' : list.length}
              </span>
              {alerts > 0 && (
                <span className="ml-auto label-caps px-[7px] py-0.5 rounded bg-negative text-white">
                  {alerts} alert{alerts === 1 ? '' : 's'}
                </span>
              )}
            </div>
            {!error && events === null && (
              <p className="text-[12px] text-on-surface-variant">Loading…</p>
            )}
            {events?.length === 0 && (
              <p className="text-[12px] text-on-surface-variant">
                No events recorded for this trade.
              </p>
            )}
            <ol className="flex flex-col">
              {list.map((event) => (
                <EventItem key={event.event_id} event={event} startMs={startMs} />
              ))}
            </ol>
          </section>
        </div>

        <footer className="bg-surface px-[18px] py-2.5 border-t border-outline-variant">
          <p className="text-[10px] text-on-surface-variant">
            Append-only. Every line was written when it happened and is never rewritten —
            read it against Kite's own order history for this trade.
          </p>
        </footer>
      </aside>
    </div>
  )
}

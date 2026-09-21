export function StopDraftControl({value, confirmed, tick, direction, disabled, onChange}: {
  value: string; confirmed: number | null; tick: number; direction: string;
  disabled: boolean; onChange: (value: string) => void
}) {
  const validTick = Number.isFinite(tick) && tick > 0
  function stepped(delta: number) {
    const base = value.trim() ? Number(value) : confirmed
    if (!validTick || base == null || !Number.isFinite(base)) return null
    const next = Number(((Math.round(base / tick) + delta) * tick).toFixed(8))
    if (next <= 0 || confirmed == null) return null
    if (direction === 'UP' ? next < confirmed : direction === 'DOWN' ? next > confirmed : true) return null
    return next
  }
  return <div>
    <label>Requested stop<input type="number" step={validTick ? tick : 'any'} value={value}
      placeholder={String(confirmed ?? '')} onChange={e => onChange(e.target.value)}/></label>
    {([-1, 1] as const).map(delta => <button key={delta} type="button"
      disabled={disabled || stepped(delta) == null}
      onClick={() => {const next = stepped(delta); if(next != null) onChange(String(next))}}>
      {delta < 0 ? '−' : '+'} 1 tick</button>)}
    <small>Tick {validTick ? tick : 'unknown'} · {direction === 'UP' ? 'BUY: raise stop to tighten' : 'SELL: lower stop to tighten'}. Changes are a draft until Request trail. Confirmed stops cannot widen.</small>
  </div>
}

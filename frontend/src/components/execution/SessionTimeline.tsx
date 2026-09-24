import { useEffect, useState } from 'react'

// The trading day in minutes after midnight IST.
const OPEN = 9 * 60 + 15
const CUTOFF = 14 * 60
const SQUARE_OFF = 14 * 60 + 50
const CLOSE = 15 * 60 + 30
const SPAN = CLOSE - OPEN

const pct = (minutes: number) => ((minutes - OPEN) / SPAN) * 100

function istNow() {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date())
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '00'
  return { minutes: Number(get('hour')) * 60 + Number(get('minute')), label: `${get('hour')}:${get('minute')}` }
}

/** Where the session is right now against the two hard times that matter:
 *  the 14:00 entry cutoff and the 14:50 square-off. */
export function SessionTimeline() {
  const [now, setNow] = useState(istNow)

  useEffect(() => {
    const id = window.setInterval(() => setNow(istNow()), 10_000)
    return () => window.clearInterval(id)
  }, [])

  const at = Math.max(0, Math.min(100, pct(now.minutes)))
  const cutoff = pct(CUTOFF)
  const squareOff = pct(SQUARE_OFF)

  return (
    <div className="flex-[0_1_560px] min-w-[300px] flex flex-col gap-[5px]">
      <div className="relative h-3.5">
        <span
          className="absolute -translate-x-1/2 font-data text-[10px] font-bold whitespace-nowrap"
          style={{ left: `${at}%` }}
        >
          NOW {now.label}
        </span>
      </div>
      <div className="relative h-2 rounded flex">
        <div className="bg-[#cfe0ff] rounded-l" style={{ width: `${cutoff}%` }} />
        <div className="bg-amber-200" style={{ width: `${squareOff - cutoff}%` }} />
        <div className="flex-1 bg-outline-variant rounded-r" />
        <span
          className="absolute -top-[3px] w-0.5 h-3.5 -ml-px bg-on-surface rounded-[1px]"
          style={{ left: `${at}%` }}
        />
      </div>
      <div className="relative h-3.5 font-data text-[10px] text-on-surface-variant whitespace-nowrap">
        <span className="absolute left-0">09:15 open</span>
        <span className="absolute" style={{ right: `${100 - cutoff}%` }}>
          14:00 cutoff
        </span>
        <span className="absolute pl-1.5" style={{ left: `${cutoff}%` }}>
          14:50 sq-off
        </span>
      </div>
    </div>
  )
}

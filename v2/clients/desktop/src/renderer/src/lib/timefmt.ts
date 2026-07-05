/**
 * WhatsApp-style time labels — the TS mirror of agentd/clients/timefmt.py.
 * One rule set for every 'when' in the app:
 *   today          -> "14:32"      (list rows)  /  "Today"     (date separators)
 *   yesterday      -> "Yesterday"
 *   within 7 days  -> "Tuesday"
 *   same year      -> "5 June"
 *   older          -> "3 Apr 1996"
 */

function startOfDay(ms: number): number {
  const d = new Date(ms)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

function dayDelta(ms: number, nowMs: number): number {
  return Math.round((startOfDay(nowMs) - startOfDay(ms)) / 86_400_000)
}

export function timeLabel(ms: number): string {
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function dayOrDate(ms: number, nowMs: number, days: number): string {
  const d = new Date(ms)
  if (days === 1) return 'Yesterday'
  if (days < 7) return d.toLocaleDateString('en-GB', { weekday: 'long' })
  if (d.getFullYear() === new Date(nowMs).getFullYear()) {
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long' }) // "5 June"
  }
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) // "3 Apr 1996"
}

/** Label for a LIST row (sessions sidebar): clock time today, else day/date. */
export function whenLabel(ms: number, nowMs = Date.now()): string {
  if (!ms) return ''
  const days = dayDelta(ms, nowMs)
  return days <= 0 ? timeLabel(ms) : dayOrDate(ms, nowMs, days)
}

/** Label for a DATE SEPARATOR between messages: 'Today' instead of a clock. */
export function dayLabel(ms: number, nowMs = Date.now()): string {
  if (!ms) return ''
  const days = dayDelta(ms, nowMs)
  return days <= 0 ? 'Today' : dayOrDate(ms, nowMs, days)
}

/** Same calendar day? — decides where date separators go. */
export function sameDay(a: number, b: number): boolean {
  return startOfDay(a) === startOfDay(b)
}

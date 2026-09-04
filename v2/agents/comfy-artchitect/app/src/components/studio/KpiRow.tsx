/* The four figures over the studio. Every number is REAL or an em-dash — a KPI card showing
 * an invented figure teaches the operator to stop reading the row. Sources:
 *   Renders    — downloaded outputs in the selected range (+ per-day bars from the same data)
 *   Avg render — mean duration of the range's completed runs
 *   VRAM       — the last probe's free/total
 *   Credits    — the account balance the composer already shows
 */

import type { StudioState } from './useStudioState'

const DAY = 86_400_000

function bars(ts: number[], days: number): number[] {
  const now = Date.now()
  const counts = Array.from({ length: 7 }, (_, i) => {
    const end = now - ((6 - i) * days * DAY) / 7
    const start = end - (days * DAY) / 7
    return ts.filter((t) => t * 1000 > start && t * 1000 <= end).length
  })
  const max = Math.max(1, ...counts)
  return counts.map((c) => Math.round((c / max) * 100))
}

function Card({
  label,
  chip,
  chipTone,
  figure,
  caption,
  viz,
}: {
  label: string
  chip?: string
  chipTone?: 'accent' | 'ok' | 'warn' | 'dim'
  figure: string
  caption: string
  viz: React.ReactNode
}) {
  return (
    <div className="st-kpi">
      <div className="st-kpi-head">
        <span className="st-kpi-label">{label}</span>
        {chip && <span className={`st-kpi-chip is-${chipTone || 'dim'}`}>{chip}</span>}
      </div>
      <div className="st-kpi-figure">
        {figure}
        <span className="st-kpi-unit">{caption}</span>
      </div>
      {viz}
    </div>
  )
}

const Bars = ({ series }: { series: number[] }) => (
  <div className="st-bars">
    {series.map((h, i) => (
      <span key={i} className={i === series.length - 1 ? 'is-now' : ''} style={{ height: `${Math.max(8, h)}%` }} />
    ))}
  </div>
)

const Meter = ({ pct, quiet }: { pct: number; quiet?: boolean }) => (
  <div className="st-meter">
    <span className={quiet ? 'is-quiet' : ''} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
  </div>
)

export function KpiRow({
  state,
  rangeDays,
  credits,
  onCredits,
}: {
  state: StudioState
  rangeDays: number
  credits: number | null
  onCredits: () => void
}) {
  const now = Date.now()
  const inRange = (ts: number) => now - ts * 1000 <= rangeDays * DAY

  const renders = (state.renders || []).filter((r) => inRange(r.ts))
  const done = (state.runs || []).filter((r) => r.status === 'complete' && inRange(r.ts))
  const avg = done.length
    ? done.reduce((s, r) => s + (r.duration || 0), 0) / done.length
    : null
  const steps = done.find((r) => r.steps)?.steps

  const vt = state.instance?.vram_total
  const vf = state.instance?.vram_free
  const usedGb = vt && vf != null ? (vt - vf) / 1024 ** 3 : null
  const usedPct = vt && vf != null ? Math.round(((vt - vf) / vt) * 100) : null

  // ≈ runs from the range's own evidence: credits ÷ (spent so far ÷ runs so far) is a guess
  // with no basis this window has — so the caption stays what it can stand behind.
  const rangeLabel = rangeDays === 1 ? 'today' : `${rangeDays} days`

  return (
    <div className="st-kpis">
      <Card
        label="Renders"
        figure={String(renders.length)}
        caption={rangeLabel}
        chip={renders.length ? undefined : undefined}
        viz={<Bars series={bars(renders.map((r) => r.ts), rangeDays)} />}
      />
      <Card
        label="Avg render"
        figure={avg != null ? avg.toFixed(1) : '—'}
        caption={avg != null ? `seconds${steps ? ` · ${steps} steps` : ''}` : 'no completed runs yet'}
        viz={<Bars series={bars(done.map((r) => r.ts), rangeDays)} />}
      />
      <Card
        label="VRAM"
        figure={usedGb != null ? usedGb.toFixed(1) : '—'}
        caption={vt ? `of ${(vt / 1024 ** 3).toFixed(0)} GB` : 'probe the instance'}
        chip={usedPct != null ? `${usedPct}%` : undefined}
        chipTone={usedPct != null && usedPct > 85 ? 'warn' : 'accent'}
        viz={<Meter pct={usedPct ?? 0} />}
      />
      <Card
        label="Credits"
        figure={credits != null ? credits.toLocaleString() : '—'}
        caption={credits != null ? 'balance' : 'no accounts service'}
        chip="Top up"
        chipTone="dim"
        viz={
          <button className="st-kpi-link" onClick={onCredits}>
            Credits &amp; billing →
          </button>
        }
      />
    </div>
  )
}

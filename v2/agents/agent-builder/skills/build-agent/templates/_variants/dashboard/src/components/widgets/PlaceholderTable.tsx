/* PLACEHOLDER WIDGET — a table with tabs and paging. See ./README.md: reuse, restyle or delete.
 *
 * @placeholder — SCAFFOLDING, not a decision. It is here to show the look, the shape and the
 * wiring; it is not what this agent is for. Adopt it (change it, rename the file, delete this
 * tag) or delete the file. `validate_agent` refuses to pack or publish while the tag remains.
 *
 * THE SECOND SHAPE A DASHBOARD NEEDS: the list behind a number. A tile says "14 anomalies"; this
 * is what you open when you want to know which ones.
 *
 * COLUMNS ARE DECLARED, not hard-coded, so the same widget serves any section: each column says
 * how to get its value out of a row and, optionally, how to render it. That is the whole reason
 * this is generic — a table per section would be four copies of pagination.
 *
 * FILTERING AND PAGING ARE LOCAL. They work on the rows they are given, which is correct for the
 * hundreds a panel fetches and wrong for the millions it should not. When your data outgrows a
 * fetch, move both server-side and pass `rows` already narrowed.
 */

import { useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, Search } from 'lucide-react'

export interface Column<Row> {
  key: string
  label: string
  /** The cell's value. Return a string for the default rendering, or JSX for your own. */
  cell: (row: Row) => React.ReactNode
  /** Right-align a numeric column. */
  align?: 'left' | 'right'
}

export const SAMPLE_TABS = ['Open', 'Resolved', 'All']

export interface SampleRow {
  id: string
  what: string
  where: string
  when: string
}

export const SAMPLE_ROWS: SampleRow[] = [
  { id: 'r1', what: 'This table is a placeholder', where: 'widgets/PlaceholderTable.tsx', when: 'now' },
  { id: 'r2', what: 'Declare your own columns', where: 'components/Dashboard.tsx', when: 'now' },
  { id: 'r3', what: 'Feed it a fetch against your tools', where: 'PanelSpec.fetch', when: 'now' },
]

export const SAMPLE_COLUMNS: Column<SampleRow>[] = [
  { key: 'what', label: 'What', cell: (r) => r.what },
  { key: 'where', label: 'Where', cell: (r) => <span className="mono">{r.where}</span> },
  { key: 'when', label: 'When', cell: (r) => r.when, align: 'right' },
]

const PER_PAGE = 8

export default function PlaceholderTable<Row extends { id: string }>({
  rows = SAMPLE_ROWS as unknown as Row[],
  columns = SAMPLE_COLUMNS as unknown as Column<Row>[],
  tabs = SAMPLE_TABS,
  onRow,
}: {
  rows?: Row[]
  columns?: Column<Row>[]
  /** Omit for a table with no tabs — the row of them disappears rather than showing one. */
  tabs?: string[]
  onRow?: (row: Row) => void
}) {
  const [tab, setTab] = useState(tabs[0] || '')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)

  /* SEARCHES WHAT IS RENDERED, not the raw object: a row whose id matches but whose visible
     columns do not would appear to match nothing. Only string cells participate — JSX cells are
     the caller's own rendering and are not searchable text. */
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return rows
    return rows.filter((r) =>
      columns.some((c) => {
        const v = c.cell(r)
        return typeof v === 'string' && v.toLowerCase().includes(q)
      }),
    )
  }, [rows, columns, query])

  const pages = Math.max(1, Math.ceil(filtered.length / PER_PAGE))
  const safePage = Math.min(page, pages - 1)
  const shown = filtered.slice(safePage * PER_PAGE, safePage * PER_PAGE + PER_PAGE)

  return (
    <div className="table-wrap">
      <div className="table-bar">
        {tabs.length > 0 && (
          <div className="table-tabs">
            {tabs.map((t) => (
              <button
                key={t}
                className={`table-tab${t === tab ? ' on' : ''}`}
                onClick={() => {
                  setTab(t)
                  setPage(0)
                }}
              >
                {t}
              </button>
            ))}
          </div>
        )}
        <label className="table-search">
          <Search size={14} strokeWidth={1.8} />
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setPage(0)
            }}
            placeholder="Filter…"
            aria-label="Filter rows"
          />
        </label>
      </div>

      <table className="table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.align === 'right' ? 'is-right' : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => (
            <tr
              key={r.id}
              className={onRow ? 'is-clickable' : undefined}
              onClick={onRow ? () => onRow(r) : undefined}
            >
              {columns.map((c) => (
                <td key={c.key} className={c.align === 'right' ? 'is-right' : undefined}>
                  {c.cell(r)}
                </td>
              ))}
            </tr>
          ))}
          {shown.length === 0 && (
            <tr>
              <td className="dash-dim" colSpan={columns.length}>
                nothing matches “{query}”
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {/* THE COUNT IS THE POINT of a pager — "8 of 214" tells you whether the filter worked.
          Hidden entirely when everything fits, because a pager with one page is furniture. */}
      {filtered.length > PER_PAGE && (
        <div className="table-foot">
          <span className="table-count">
            {safePage * PER_PAGE + 1}–{Math.min(filtered.length, (safePage + 1) * PER_PAGE)} of{' '}
            {filtered.length}
          </span>
          <div className="table-pager">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={safePage === 0}
              title="Previous page"
              aria-label="Previous page"
            >
              <ChevronLeft size={15} strokeWidth={1.8} />
            </button>
            <button
              onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
              disabled={safePage >= pages - 1}
              title="Next page"
              aria-label="Next page"
            >
              <ChevronRight size={15} strokeWidth={1.8} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

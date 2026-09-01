import { Search, X } from 'lucide-react'
import type { KeyboardEvent } from 'react'

/**
 * THE search box — a Search icon + a borderless input inside one pill, plus a clear (×) button
 * that appears as soon as there's text and empties the field. EVERY search bar in the app renders
 * this, so the clear affordance and behaviour are identical everywhere (change it here, change it
 * everywhere). Per-context size/placement comes from `className` (the .search / .pill-search /
 * .entity-search / .nav-search aliases in styles.css) — the base `.search-box` chrome is shared.
 */
export default function SearchBox({
  value,
  onChange,
  placeholder = 'Search',
  className = '',
  iconSize = 15,
  autoFocus = false,
  onKeyDown,
  onBlur
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  className?: string
  iconSize?: number
  autoFocus?: boolean
  onKeyDown?: (e: KeyboardEvent<HTMLInputElement>) => void
  onBlur?: () => void
}) {
  return (
    <div className={`search-box ${className}`.trim()}>
      <Search size={iconSize} />
      <input
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={onBlur}
      />
      {value !== '' && (
        <button
          type="button"
          className="search-clear"
          title="Clear search"
          aria-label="Clear search"
          // keep focus in the input (and don't trip an onBlur-close) when clearing
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onChange('')}
        >
          <X size={14} />
        </button>
      )}
    </div>
  )
}

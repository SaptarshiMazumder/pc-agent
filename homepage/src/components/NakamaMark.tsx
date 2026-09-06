/**
 * The nakama link — two woven rings, the product's mark. Inlined rather than
 * <img>'d so it can inherit currentColor and animate on hover.
 * Geometry is identical to v2/clients/ui/src/assets/nakama.svg.
 */
export function NakamaMark({ size = 32, title }: { size?: number; title?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 256 256"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role={title ? 'img' : 'presentation'}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      className="nakama"
    >
      <defs>
        <mask id="nakama-cut-b">
          <rect width="256" height="256" fill="#fff" />
          <circle cx="154" cy="128" r="40" fill="none" stroke="#000" strokeWidth="22" />
        </mask>
        <mask id="nakama-cut-a">
          <rect width="256" height="256" fill="#fff" />
          <path
            d="M115.7 90.4 A40 40 0 0 1 137.3 109.2"
            fill="none"
            stroke="#000"
            strokeWidth="22"
            strokeLinecap="round"
          />
        </mask>
      </defs>

      <circle
        cx="102"
        cy="128"
        r="40"
        fill="none"
        stroke="currentColor"
        strokeWidth="12"
        mask="url(#nakama-cut-b)"
      />
      <circle
        cx="154"
        cy="128"
        r="40"
        fill="none"
        stroke="currentColor"
        strokeWidth="12"
        mask="url(#nakama-cut-a)"
      />
      <path
        d="M115.7 90.4 A40 40 0 0 1 137.3 109.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="12"
        strokeLinecap="round"
      />
    </svg>
  )
}

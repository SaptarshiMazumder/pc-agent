# agentd homepage

The public marketing site. A static single page — no backend, no API calls, no
third-party requests at runtime (fonts are bundled, not fetched from a CDN).

Kept **outside `v2/`** on purpose: it shares the product's design language but
none of its code, and it must stay up when the platform is paused.

## Stack

React + TypeScript + Vite, built to `dist/`. Icons are `lucide-react`, matching
the desktop client.

## Design

Tokens in [`src/styles/tokens.css`](src/styles/tokens.css) are **ported from the
product** (`v2/clients/ui/src/styles.css`) — same warm paper/charcoal dual theme,
same lime accent, same type stack (Bricolage Grotesque · Hanken Grotesk ·
JetBrains Mono). Surface, text and accent values are the same numbers, so the
site and the app read as one product. Anything marketing-only is namespaced
`--site-*`.

Never hardcode a color in a component; extend the tokens instead.

Theme follows the OS by default and is overridable by the header toggle. The
choice is persisted, and an inline script in `index.html` applies it before first
paint so dark-mode visitors never see a light flash.

## Develop

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # type-check, then emit dist/
npm run preview    # serve the built output
```

## Layout

```
src/
  sections/    one file per page section, in page order (see App.tsx)
  components/  reusable pieces — TerminalDemo, LoopDiagram, AgentFileTree, nav, footer
  data/        page content as plain data (agents, tools) — no JSX
  lib/         hooks (reveal, theme, reduced-motion) and the icon registry
  styles/      tokens.css (design system) · base.css (reset + primitives) · sections.css
public/        favicon and the OG card
infra/         Terraform: S3 + CloudFront (see infra/README.md)
scripts/       deploy.sh
```

Content lives in `src/data/` where it can — editing the agent gallery or the
tool grid should not mean touching a component.

## Accessibility

Non-negotiable, and already handled — keep it that way when editing:

- All body and label text meets 4.5:1. `--faint` is **decorative only** (icons,
  ordinal numbers); anything a reader needs uses `--dim` or `--text`.
- Every animation stops under `prefers-reduced-motion`. JS-driven animations
  (the typing terminal) render their **finished** state, never an empty one.
- A skip link is the first tab stop; the mobile menu closes on Escape.
- Emphasis is never carried by color alone (the hero's accent word also has an
  underline).
- Interactive targets are at least 48px.

## Deploy

Target: **https://homepage.thorgodofthunder.site**

Deliberately **not** part of `v2/deploy/scripts/redeploy.sh`. That script rolls
containers onto ECS and is governed by the platform's `paused`/`hibernate`
switches; a marketing site that goes dark whenever the backend sleeps is a funnel
that leaks. This has no container and no service, so it gets its own state and its
own lifecycle — the same reasoning the marketplace uses.

See [`infra/README.md`](infra/README.md) for the domain and cost details. Short
version, once AWS credentials are in the environment:

```bash
cd infra && terraform init && terraform apply    # one time
cd .. && ./scripts/deploy.sh                     # build, sync, invalidate
```

# design-sync notes — Comfy Artchitect UI

Source package: `v2/agents/comfy-artchitect/app` (a Vite APP, not a library — no dist, no build
to run; the converter synthesizes the entry from `src/`).

## Repo-specific setup a fresh clone must recreate

- **Self-junction**: `v2/agents/comfy-artchitect/app/node_modules/agent-app -> ..`
  (`cmd /c "mklink /J agent-app .."` from inside `node_modules`). The converter resolves the
  package by name under `--node-modules`; an app repo doesn't self-install.
  **CREATE IT FOR THE SYNC, REMOVE IT AFTER** (`cmd /c "rmdir agent-app"`): it is a directory
  CYCLE, and the agent platform's own validator/packer walk the agent tree — with the junction
  in place `validate_agent` on comfy-artchitect hangs forever. This bit us once already.
- **SDK shim**: `v2/agents/comfy-artchitect/app/node_modules/@agentd/client/package.json` with
  `main`/`types` pointing at `../../../vendor/agentd-client.js|.d.ts`. The app resolves
  `@agentd/client` via a vite alias the converter can't see; tsconfig `paths` maps only the
  types half, so setting `cfg.tsconfig` instead would bundle a `.d.ts` as runtime code.
- **Fork link**: `.design-sync/node_modules -> ../.ds-sync/node_modules` junction (the
  source-kit fork imports `ts-morph` bare).
- Playwright: cached chromium builds were 1181/1228 → `playwright@1.54.0` (pins 1181) into
  `.ds-sync/`.

## Why source-kit.mjs is forked (see cfg.libOverrides)

1. Components are **default exports**; `export * from` in the synth entry re-exports none of
   them — the fork recovers each declared name textually and re-exports it.
2. `src/main.tsx` is the app bootstrap (`createRoot(...).render()` at module scope) — bundled
   into the entry it throws on any page without `#root` and kills the whole IIFE. Excluded.
3. The bootstrap was also what imported `tokens.css` / `theme.css` / `styles.css` (order
   load-bearing: theme overrides tokens); the fork's entry imports them instead.

## Preview facts (previews/ is authored, committed)

- The DS is **dark**; the card chrome is white. `cfg.provider` = `PreviewSurface` (from
  `.design-sync/preview-surface.jsx` via `cfg.extraEntries`) paints `var(--bg)`/`var(--text)`
  + `overflow: hidden` (the theme's ambient glow is an absolutely-positioned child that
  otherwise bleeds past the cell).
- `extraEntries` paths resolve from the **junctioned** package dir
  (`app/node_modules/agent-app`), hence the six `../`.
- `Thread`/`MessageItem` user items REQUIRE `files: []`.
- `SignIn` positions `fixed` over the window — its preview wraps it in a
  `transform: translateZ(0)` container (containing-block trick) or the card clips.
- `Settings` renders populated by stubbing `client.request('config.get')` with a ConfigData
  payload (fields: `settings`, `settingsValues`, `values`, `providerKeys`, `env`).
- `Credits`/`OrgView` render their honest no-service/failed-status states — expected, not bugs.
- `RunModeBadge` renders null until a live daemon answers; its authored cell is a typographic
  explanation. There is deliberately no fake badge.
- Media artifacts (image kind) would hit the daemon's `/file` endpoint — previews use
  `kind: 'file'` cards only.

## Known render warns

- (none currently — render check clean at last full run)

## Re-sync risks

- The **junctions and the shim package live under `node_modules`** — a fresh install/clone
  loses all three; recreate per the setup section or the build fails at `agent-app` /
  `@agentd/client` resolution.
- `Settings.tsx` preview inlines a ConfigData payload — if `useSettings.ts` renames fields
  (`settings`/`settingsValues`), the page silently renders empty rows again.
- The `.d.ts` files are synth-mode **stubs** (`[key: string]: unknown`) — component prop types
  in the app are inline anonymous types the extractor can't lift. `cfg.dtsPropsFor` per
  component would fix this; not done this run (the `.prompt.md`s + conventions header carry the
  API knowledge instead).
- The app's components import `zustand` stores and the vendored SDK at module level; new
  components with module-level side effects (like main.tsx's) would need the same fork
  exclusion treatment.
- `comfy-artchitect/app` is regenerated tooling territory: if the agent's window is ever
  re-scaffolded from the skeleton template, previews keep working only while component names
  and props stay compatible.

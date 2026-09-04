# Building with Comfy Artchitect UI

This is a **dark, token-driven agent-window kit** (React 18). Nothing is colored inline — every
value resolves from CSS custom properties defined in `tokens/` and the stylesheet closure.

## The ground rule (do this first)

Give your app root the window's ground, or text renders light-on-white and invisible:

```jsx
<div style={{ background: 'var(--bg)', color: 'var(--text)', minHeight: '100vh' }}>
  {/* everything */}
</div>
```

No provider component is needed — components read only CSS variables from the stylesheet.

## Styling idiom: tokens, not classes

Style your own layout glue with `var(--*)` tokens; do not invent CSS classes (library class
names like `.bubble` are component-internal). The vocabulary:

- **Surfaces**: `--bg` (window), `--bg2` (card), `--bg3` (inset), `--bg4` (hover);
  borders `--border`, `--border2`
- **Ink**: `--text` (headings), `--body` (prose), `--dim`, `--faint`
- **Accent**: `--accent`, `--accent-soft` (tint bg), `--accent-edge` (border),
  `--accent-text`; primary buttons use `--prim-bg` / `--prim-ink`
- **Status**: `--ok`, `--warn`, `--danger`, `--danger-soft`
- **Type**: families `--sans` / `--mono` / `--display`; sizes `--fs-hero`, `--fs-title`,
  `--fs-lead`, `--fs-body`, `--fs-meta`, `--fs-label` (with `--tracking-label`);
  weights `--fw-normal`, `--fw-medium`, `--fw-semi`, `--fw-heavy`
- **Shape**: radii `--r-card`, `--r-bubble`, `--r-control`, `--r-pill`, `--r-sm`;
  shadows `--shadow`, `--shadow-card`
- **Space**: `--sp-tight`, `--sp-control`, `--sp-gap`, `--sp-card`, `--sp-row`, `--sp-panel`
- **Motion**: `--dur-fast`, `--dur-base`, `--dur-slow` with `--ease-smooth`

Fonts (Plus Jakarta Sans, JetBrains Mono) ship inside `_ds_bundle.css` — never link a font host.

## Composition facts that bite

- `Thread` takes `items` of typed kinds (`user`/`bot`/`think`/`tool`/`system`); a `user` item
  **requires `files: []`** even when empty. `MessageItem` renders one item.
- `Composer` is fully controlled: `running`, `pending`, `connected`, `model`, `credits` and the
  `on*` handlers are required-ish — pass real values, it draws its states from them.
- `WorkflowShelf` derives its cards from `artifacts` whose `name`s end `.api.json` / `.json`.
- `Sidebar` wants the window's one `account` object and an `extraDestinations` array for
  app-specific views; `SignIn` positions `fixed` over the window (gate it, don't embed it).
- Settings rows: `DeclaredField` (author-declared fields), `Field` (daemon knobs),
  `SecretField` (write-only credentials).

## Where the truth lives

Read `styles.css` (and its `@import`ed `_ds_bundle.css` + `tokens/`) before styling anything;
each component's props are in `components/<group>/<Name>/<Name>.d.ts` and its usage in the
matching `.prompt.md`.

## One idiomatic build

```jsx
<div style={{ background: 'var(--bg)', color: 'var(--text)', minHeight: '100vh', padding: 'var(--sp-panel)' }}>
  <div style={{ background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 'var(--r-card)', padding: 'var(--sp-card)' }}>
    <h2 style={{ font: 'var(--fw-heavy) var(--fs-title) var(--sans)', color: 'var(--text)', margin: 0 }}>Runs</h2>
    <Thread running={false} items={[{ kind: 'user', files: [], text: 'Build me a workflow' }]} />
    <Composer running={false} pending={[]} connected={true} model="claude-sonnet-5" credits={1200}
      maxFiles={8} onSend={() => {}} onAbort={() => {}} onFiles={() => {}} onRemoveFile={() => {}} onCredits={() => {}} />
  </div>
</div>
```

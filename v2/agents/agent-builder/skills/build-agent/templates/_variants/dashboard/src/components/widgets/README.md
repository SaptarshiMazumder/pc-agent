# Placeholder widgets

**Everything in this folder is a PLACEHOLDER.** It exists so a new dashboard agent has a window
worth looking at before it has a single tool of its own — and so the shapes a dashboard is made of
(a number with a trend, a chart, a list of what happened, a table) are already written in this
template's style, with the hooks wired.

## For whoever builds an agent from this template

Treat these as **yours to reuse, restyle, rename or delete**. They are not shared modules, nothing
validates them, and no other agent depends on them. Specifically:

- **Keep one** and feed it real data — that is the fastest path. Every widget takes plain props
  and renders; none of them fetches anything itself.
- **Delete the rest.** A dashboard showing four invented numbers is worse than a dashboard showing
  one real one. Deleting a widget means deleting its file and its entry in `PANELS` /
  `SECTIONS` — nothing else refers to them.
- **Write your own** beside them in this folder, in the same shape: props in, JSX out.

Every file here is named `Placeholder*` for exactly one reason: so that a glance at the imports in
`Dashboard.tsx` tells you what is still scaffolding and what is the agent's own. When a widget
stops being a placeholder, rename it.

## What is NOT a placeholder

- `PanelSpec` and the `Panel` component in `../Dashboard.tsx` — the fetch/refresh/state machinery,
  including the four states (resolved, loading, failed, not-connected). That is the template's
  actual contribution and it is worth keeping.
- Anything under `src/common/` — sign-in, credits, organizations, settings. Those are copied
  verbatim into every agent and `validate_agent` diffs them. Their **CSS is yours**; their fields
  and flows are not.

## The sample data

Each widget ships a small exported `SAMPLE_*` constant so the window renders before any tool
exists. Those constants are the first thing to delete once a real `fetch` is feeding the widget —
a chart that silently falls back to sample data is the dashboard failure the panel states exist to
prevent.

## The four files here that this README does not list

`PlaceholderLibraryView`, `PlaceholderNote`, `PlaceholderResultCard` and `PlaceholderStat` come
from the base template, which every agent is built on — the base is the *chat* shape, and those
four are its placeholders. Nothing in this template imports them, so they cost nothing at runtime;
they are still tagged `@placeholder`, so **delete all four** unless you are adopting one. The
result card is the only one worth a second look: an answer that is a thing rather than a paragraph
renders the same in a panel as it does in a transcript.

# Porting the Claude design into the agentd desktop client

This folder recreates the Claude design as real React/TS + CSS for
`v2/clients/desktop`. Copy the files to the paths below, then apply the small
`store.ts` diff. Target: identical look and behaviour.

All paths are relative to `v2/clients/desktop/src/renderer/`.

---

## 1. Dependencies

Icons use **lucide-react**. From `v2/clients/desktop`:

```
npm i lucide-react
```

(`react-markdown` + `remark-gfm` are already in your deps — MessageItem still uses them.)

## 2. Fonts

Add to `renderer/index.html` inside `<head>`:

```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link
  href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&family=Hanken+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap"
  rel="stylesheet"
/>
```

> Offline build? Vendor the three families locally and swap the `<link>` for
> `@font-face` — the CSS only references the family names.

## 3. Copy files

| From (this folder)                 | To                                              |
| ---------------------------------- | ----------------------------------------------- |
| `styles.css`                       | `src/styles.css` (replace)                      |
| `lib/agentPresentation.ts`         | `src/lib/agentPresentation.ts` (new)            |
| `components/TabBar.tsx`            | `src/components/TabBar.tsx` (new)               |
| `components/Sidebar.tsx`          | `src/components/Sidebar.tsx` (replace)          |
| `components/ChatView.tsx`         | `src/components/ChatView.tsx` (replace)         |
| `components/MessageItem.tsx`      | `src/components/MessageItem.tsx` (replace)      |
| `components/SettingsView.tsx`     | `src/components/SettingsView.tsx` (replace)     |
| `components/StoreView.tsx`        | `src/components/StoreView.tsx` (replace)        |

Also copy `assets/nakama.svg` if you tweaked it (the current one is fine — it's
already green-only and works on any background).

`App.tsx` and `main.tsx` need **no changes**.

## 4. `store.ts` — the only logic change

Your store already has `theme` / `toggleTheme` / `initialTheme` / `applyTheme`.
Add tabs + sidebar-collapse. Four edits:

**(a) Apply the theme at boot** so a persisted `dark` actually shows on first
paint. In `bootstrap()`, add as the first line:

```ts
applyTheme(get().theme)
```

**(b) Extend the `AppState` interface** (near the other fields/actions):

```ts
  openTabs: string[]
  sidebarCollapsed: boolean
  closeTab(sessionId: string): void
  reorderTabs(from: string, to: string): void
  toggleSidebar(): void
```

**(c) Seed the new state** (in the returned object, next to `sessions: {}`):

```ts
    openTabs: [],
    sidebarCollapsed: false,
```

**(d) Add the actions** (anywhere among the other actions in the returned
object). Note the small edits to `newSession` / `resumeSession` / `deleteSession`
so tabs track the open chats:

```ts
    toggleSidebar() {
      set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed }))
    },

    closeTab(sessionId) {
      const { openTabs, currentSessionKey } = get()
      const tabs = openTabs.filter((t) => t !== sessionId)
      set({ openTabs: tabs })
      if (currentSessionKey === sessionId) {
        const idx = openTabs.indexOf(sessionId)
        const next = tabs[idx] || tabs[idx - 1] || tabs[tabs.length - 1]
        if (next) void get().resumeSession(next)
        else get().newSession()
      }
    },

    reorderTabs(from, to) {
      set((s) => {
        const a = [...s.openTabs]
        const fi = a.indexOf(from)
        const ti = a.indexOf(to)
        if (fi < 0 || ti < 0) return {}
        a.splice(fi, 1)
        a.splice(ti, 0, from)
        return { openTabs: a }
      })
    },
```

Then make the three existing actions register the open tab. Replace **`newSession`**:

```ts
    newSession(projectId?: string) {
      const key = newSessionKey()
      set((s) => ({
        currentSessionKey: key,
        currentProjectId: projectId || '',
        view: 'chat',
        openTabs: s.openTabs.includes(key) ? s.openTabs : [...s.openTabs, key]
      }))
    },
```

In **`resumeSession`**, the first `set({...})` becomes (add the `openTabs` line):

```ts
      set((state) => ({
        currentSessionKey: sessionId,
        currentProjectId: row?.projectId || '',
        view: 'chat',
        openTabs: state.openTabs.includes(sessionId) ? state.openTabs : [...state.openTabs, sessionId]
      }))
```

In **`deleteSession`**, also drop it from `openTabs` — in the first `set`,
add to the returned object:

```ts
        openTabs: state.openTabs.filter((t) => t !== sessionId),
```

That's it. `selectAgent` already routes through `resumeSession` / `newSession`,
so agent switches open/focus tabs automatically.

## 5. Run

```
npm run dev
```

You should get: warm dual-theme shell (toggle bottom-left of the sidebar or in
Settings), Chrome-style chat tabs (scroll + overflow menu + drag-reorder),
collapsible icon-rail sidebar with a chat search, the low aurora glow behind the
chat (stronger on the empty state), lime-tinted user bubbles, and lucide icons
throughout.

---

## Notes & parity details

- **Theme** persists to `localStorage` (your existing `applyTheme`). Default is
  **light** per your `initialTheme()`; change that if you want dark by default.
- **Tabs** are keyed by `sessionKey`. A brand-new chat has no server row yet, so
  its tab reads **“New chat”** until the server auto-titles it — same as the live app.
- **Tab dot colour** uses the current agent's colour. If you want per-tab colours
  for chats belonging to other agents, store an `agentId` alongside each tab and
  look it up in `TabBar` (a small extension).
- **Reasoning / computer-use / notifications** in Settings are client-side UI
  only — the real values live in the daemon config. Wire them to real
  gateway calls when you're ready.
- **Aurora intensity** is CSS-only: `.chat::before` is faint; `.chat.empty::before`
  is full. Tune the `--aur-*` tokens in `:root` / `[data-theme='dark']`.
- **`agentPresentation.ts`** holds the agent taglines/colours. Move these into
  `agent.toml` (e.g. a `tagline` field) and read them from `hello.agents` when you
  want them authored server-side instead of hard-coded.
- The **traffic-light titlebar** from the Claude mock is intentionally omitted —
  your Electron window already has native chrome. Add one back only if you switch
  to a frameless window.

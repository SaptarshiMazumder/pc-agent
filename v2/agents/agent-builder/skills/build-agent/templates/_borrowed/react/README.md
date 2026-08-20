# The React starter

Four config files and a vendored SDK. **No app code** — that is the point. What the window
should be is a judgement about this particular agent, and the working examples to judge from are
the agents under `agents/samples/`. Read them, decide, then write `src/`.

```
package.json        react + vite + typescript. NOTE what is absent: @agentd/client.
vite.config.ts      base: './', outDir: '../ui', and the SDK alias
tsconfig.json       strict, plus the `paths` entry that types the aliased SDK
index.html          the entry vite compiles
vendor/             the SDK, as a file. Do not edit; the SDK's own build refreshes it.
```

## Why the SDK is vendored and not a dependency

The samples in this repo declare it as a path dependency:

```json
"@agentd/client": "file:../../../../clients/sdk-js"
```

That resolves **only inside this repo**. An agent that lives in the user's own agents directory
has nothing at that path, so `npm install` fails and the app can never be built — by everyone
except its author, who is the one person who will not notice.

So the bundle and its types sit in `vendor/`, aliased in `vite.config.ts` for the bundler and in
`tsconfig.json` for the compiler. No registry, no relative escape out of the agent, no install
step that can fail. If you copy a sample's `package.json`, **delete the `@agentd/client` line** —
the alias already provides it, and the dependency will only break the build somewhere else.

## Layout

```
app/     source — this directory. Rebuild here.
ui/      BUILT OUTPUT (vite outDir). This is what ships and what the daemon serves.
```

`agent.toml` points at the build:

```toml
[app]
entry = "ui/index.html"
```

Build before you hand the agent over, and rebuild after every source change — the daemon serves
`ui/`, so an unbuilt change is invisible no matter how correct it is:

```
cd app && npm install && npm run build
```

Only the author needs Node. Whoever installs the agent gets the built `ui/`.

## Before you write a line of `src/`

Read `agents/samples/`. Every one of them is a working agent, and between them they cover the
things that are easy to get wrong and impossible to notice: ordering a turn's blocks so text,
reasoning and tool calls interleave the way they happened; streaming the model's thinking so a
long tool run is not a frozen list; waiting for the socket before the first request; rendering
the reason a run failed instead of a shrug.

They are references, not a mould. Take the mechanism, not the layout, and build what this agent
actually needs.

## Two files arrive already written

`src/main.tsx` signs the user in before the first render, and `src/Credits.tsx` is the Credits &
billing page. Neither is a judgement about your agent, which is why they ship — every agent with a
window does both.

**`main.tsx` needs nothing from you.** `Credits.tsx` does: give it its own view, reached from a nav
entry beside Settings.

```tsx
import Credits from './Credits'
...
{view === 'credits' && <Credits />}
```

Not a section inside your settings screen — topping up is what a user comes looking for the
moment a run stops, and settings is where you go to change how the thing works.

`validate_agent` reports `UI_NO_CREDITS` until something renders it, and that error blocks
`package_agent` and `publish_agent`. Shipping the file is not the same as having the page.

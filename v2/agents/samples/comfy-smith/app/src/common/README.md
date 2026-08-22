# `_common/` — the modules every agent gets, copied verbatim

Accounts and money. Two things every agent with a window needs, that are **not** a judgement about
what your agent is — so they arrive already written instead of as rules to remember.

`scaffold_react_app` copies this whole folder into a new agent's `app/src/common/`. Import from
there. **Do not rewrite these files.** `validate_agent` compares your copy against this source and
reports `UI_COMMON_MODIFIED` if they differ.

## Why copied, and not a package

The real logic already lives in one place — the SDK (`@agentd/client`), vendored into every agent.
These files are the thin React layer over it: a hook, a gate call, two screens. They are copied
rather than imported because an agent is a **shipped artifact**: it is packaged, published,
downloaded and run on someone else's machine, where nothing resolves a workspace path. A copy in
the agent's own tree is the only version that survives that trip.

So the split is deliberate:

| | lives in | why |
|---|---|---|
| talking to accounts, tokens, renewal, checkout | the SDK | one implementation, or two sets of credential bugs |
| the React glue around it | here, copied | must ship inside the agent |

## What is in it

```
auth/
  useAuth.ts        signedIn · email · signIn() · signOut() · run mode
  SignIn.tsx        the gate, before the first render
  ProfileMenu.tsx   the account menu — sign in/out, and a way to reach Credits
credits/
  Credits.tsx       the Credits & billing page
```

## What you still have to do

**Render them.** Copying is not wiring — `validate_agent` reports `UI_NO_SIGN_IN` and
`UI_NO_CREDITS` until something actually puts them on screen.

- `SignIn` goes in `main.tsx`, **before** the first render.
- `Credits` gets **its own view**, reached from a nav entry beside Settings. Not a section inside
  your settings screen: topping up is what a user comes looking for the moment a run stops.
- `ProfileMenu` goes wherever your window shows who is signed in — usually the bottom of a sidebar.

## What they need from the rest of the system

Nothing. No Python, no `agent.toml` entry, no settings, no plugin.

They talk to the accounts service over ordinary HTTP and ask the daemon exactly one question —
"where is that service?". The only requirement is install-wide, not per-agent: an `accounts_url` in
the build's `distribution.toml`. Where there is none — a bring-your-own-key build — every one of
these renders **nothing**, which is why they are safe to include unconditionally.

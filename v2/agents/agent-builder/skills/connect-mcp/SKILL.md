---
name: connect-mcp
description: Use when an agent you are building needs to reach a THIRD-PARTY SERVICE — AWS, GitHub, Notion, Slack, Stripe, a database, any product with an API — instead of writing your own tools for it. Covers finding the MCP server, declaring it so it survives publishing, credentials, sign-in, and verifying it actually works.
---

# Connecting an agent to an MCP server

An MCP server is a bundle of tools somebody else already wrote. Connecting one is how an agent
gets a hundred tools you did not have to build.

**Declare it in the agent's `agent.toml`. Never leave it in the daemon's config.**

```toml
[[settings]]
key      = "ACME_API_KEY"
label    = "Acme API key"
kind     = "secret"
required = true

[[mcp]]
name    = "acme"                                           # the tool namespace -> acme__*
command = ["uvx", "acme-mcp-server@latest"]                # stdio; OR url = "https://…"
env     = { ACME_API_KEY = "${ACME_API_KEY}" }             # ${…} names a [[settings]] key
```

The server above is a stand-in. **There is no list of real ones anywhere in your instructions,
deliberately** — there was, briefly, and the command it recorded was missing a required argument,
so every agent built from it shipped a server that could never start. Look the real one up, and
confirm what it exposes with `mcp_status` rather than trusting what you read. That loop works for
any service; a remembered command line only works until it changes.

## Why not `add_mcp`

`add_mcp` connects a server and writes it into **this machine's** `agentd.config.json`. That file
is not packaged. So an agent built that way works on your machine, gets published, and every
person who installs it receives an agent whose `acme__*` tools do not exist — and the only symptom
is the model saying it cannot do the thing.

Use `add_mcp` to TRY a server while you are still figuring out whether it works. Put the working
result in `agent.toml`. That is the copy that travels.

## The three situations

**A public server.** Read its documentation. Extract four things: the transport (a `command` to
launch, or a `url` to call), the exact arguments, the credentials it reads, and the tools it is
supposed to expose. Declare the credentials as `[[settings]]` so the person installing the agent
supplies their own.

**A private or unlisted server.** The user pastes the details — an endpoint, a token, sometimes a
manifest. Take what they give you. **Never invent an endpoint.** A guessed URL that happens to
resolve is worse than telling the user you need the real one, because it fails somewhere far away
from the guess.

**A server the user wrote.** Same as private, plus: ask whether it is stdio or HTTP, since people
usually know how they run it rather than what it is called.

## Credentials

Every `${NAME}` in an `[[mcp]]` block must be a `[[settings]]` key of the same agent. That is not
a style rule — the daemon **refuses to connect** a server whose referenced settings are empty,
precisely so an agent can never quietly run on whatever credentials the daemon itself happens to
hold. A missing key produces "needs ACME_API_KEY", not a connection to the wrong account.

**Never inline a real credential into `agent.toml`.** That file ships. Writing
`env = { ACME_API_KEY = "sk-live-7f3…" }` publishes your key to everyone who installs the agent.

Two agents may declare the same server name and the same setting names and mean completely
different accounts — a read-only cost-monitoring key and a provisioning key, say. That works:
values are stored per agent.

## When the server wants a sign-in, not a key

**A 401 carrying a `WWW-Authenticate` header means OAuth.** So does a docs page that talks about
"connect your account" and never mentions an API key. Do not go hunting for a key that does not
exist — declare the sign-in:

```toml
[[oauth]]
name   = "myhealth"
server = "https://api.myhealth.app"     # endpoints discovered from here
scopes = ["read:records"]

[[mcp]]
name = "myhealth"
url  = "https://api.myhealth.app/mcp"
auth = "oauth:myhealth"                 # instead of a headers line
```

The user presses Connect on the agent's settings page, signs in, and the daemon holds the token
and refreshes it. If the provider requires an app to be registered first (most of the big ones
do), add `authorize_url`, `token_url`, and `client_id`/`client_secret` as `[[settings]]` keys —
the person installing the agent registers their own app. **Never hard-code yours.**

`mcp.status` reports `not signed in` for a server whose connection is not there yet, which is the
same kind of answer as a missing key: something the user does, not an error you can fix.

## Verify before you claim it works

**An MCP server that connects and exposes no tools looks exactly like one that worked.** So do not
report success from the fact that nothing errored.

1. Reload the agent (`reload_agent`) — this also drops whatever the daemon concluded about the
   PREVIOUS declaration, so your edit actually gets a fresh attempt.
2. **`mcp_status(agent_id='<id>')`** — it dials anything untried and reports, per server, the
   problem and the tools it really exposed.
3. Read back the tool names. Wire your skills and UI to THOSE, not to what the docs promised.

**When an agent you built has no tools, this is the first thing you do — not the last.** The
daemon already knows why; a shell command that pokes at the package does not. An agent-builder
once spent twenty minutes rediscovering a reason this tool would have printed in one call.

What the `problem` string tells you:

- **`needs <SETTING>`** — the USER has not filled that field in on the agent's settings page.
  Nothing to fix in the config: say which field, and where, and wait for them.

  **Referencing a setting in an `[[mcp]]` env block is what makes it required** — `required =
  false` does not change that, because the daemon refuses any server whose referenced settings
  are empty. So this message is expected on a fresh agent, and it is the mechanism working.
  Never remove the `${…}` reference, mark the field optional, or supply the value yourself to
  clear it: each of those starts the server on credentials the owner never chose.
- **anything else** — yours. A wrong command, a missing argument, a package that does not exist.
  Check it against the server's own documentation before changing anything else.
- **no problem but no tools** — the server started and offered nothing. Usually the wrong server,
  or one that needs an argument to know what to serve.

## What to tell the user when you finish

- which server, and whether it launches a process or calls a URL
- the tools it actually exposed, by name — from `mcp_status`, not from its documentation
- **which fields they still have to fill in, and where.** An agent whose settings are empty has
  no tools and cannot do the thing it was built for. Handing that over without saying so is
  handing over something broken.
- nothing about being local-only. Settings are stored **per account** — each person who uses the
  agent fills in their own, and a hosted daemon keeps them apart. An agent with `[[settings]]` or
  `[[mcp]]` ships to the web like any other.

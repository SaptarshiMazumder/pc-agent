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
key      = "AWS_ACCESS_KEY_ID"
label    = "AWS access key"
kind     = "secret"
required = true

[[mcp]]
name    = "aws"                                            # the tool namespace -> aws__*
command = ["uvx", "awslabs.aws-api-mcp-server@latest"]     # stdio; OR url = "https://…"
env     = { AWS_ACCESS_KEY_ID = "${AWS_ACCESS_KEY_ID}" }   # ${…} names a [[settings]] key
```

## Why not `add_mcp`

`add_mcp` connects a server and writes it into **this machine's** `agentd.config.json`. That file
is not packaged. So an agent built that way works on your machine, gets published, and every
person who installs it receives an agent whose `aws__*` tools do not exist — and the only symptom
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
hold. A missing key produces "needs AWS_ACCESS_KEY_ID", not a connection to the wrong account.

**Never inline a real credential into `agent.toml`.** That file ships. Writing
`env = { AWS_ACCESS_KEY_ID = "AKIA…" }` publishes your key to everyone who installs the agent.

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

1. Reload the agent (`reload_agent`).
2. Call `mcp.status` for it.
3. Read back the tool names it actually reports.

If `mcp.status` shows a `problem`, say what it is — the string is written to be shown to the user.
Two are normal and both need the user, not you:

- **`needs <SETTING>`** — they have not filled that field in on the agent's settings page yet.
- **`waiting for approval to run: <command>`** — a stdio server wants to launch a process on their
  machine, and they approve the exact command once, in the same place. Tell them what the command
  is and why the agent needs it. Do not treat this as an error; it is the system working.

A `url` server never needs approval — nothing runs locally.

## What to tell the user when you finish

- which server, and whether it launches a process or calls a URL
- the tools it actually exposed, by name
- which fields they still have to fill in, and where
- that an agent with `[[settings]]` or `[[mcp]]` is **local-only** for now: the values live in
  their machine's `.env`, and a hosted daemon has one `.env` shared by every account

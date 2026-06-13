# agentd

Minimal terminal agent: a WebSocket gateway daemon running an LLM ⇄ tool loop,
plus a thin terminal client. Any LLM via LiteLLM.

## Architecture

```
terminal client (python -m client)
    │  WebSocket: chat.send {sessionKey, message, idempotencyKey}
    ▼
gateway (python -m agentd)            ← responds {runId} immediately, runs async
    │  agent loop: LLM → tool calls → execute → results → LLM … until done
    ▼
events streamed back (chat.event)     ← text deltas, tool activity, lifecycle
transcripts persisted as JSONL        ← ~/.agentd/sessions/<sessionKey>.jsonl
```

Tools: `read` `write` `edit` `exec` `process` `web_search` `web_fetch` `browser`

## Setup

```
pip install -r requirements.txt
playwright install chromium        # for the browser tool
```

Set a provider API key for LiteLLM (any of):

```
GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY / ...
AGENTD_MODEL=gemini/gemini-2.5-flash   (any LiteLLM model id)
BRAVE_API_KEY=...                      (optional; web_search falls back to DuckDuckGo)
```

Optionally copy `config.example.json` → `agentd.config.json`.

## Run

Terminal 1: `python -m agentd`
Terminal 2: `python -m client` (`--session <id>` to resume, `--url ws://...`)

Client commands: `/abort` `/new` `/quit`

## Tests

```
python -m pytest tests/
```

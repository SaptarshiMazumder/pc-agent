# agentd

Minimal terminal agent: a WebSocket gateway daemon running an LLM ⇄ tool loop,
plus a thin terminal client. Any LLM via LiteLLM. Self-contained: keys come from
`v2/.env`, runtime data goes to `v2/.agentd/`, packages live in `v2/.venv/`.

## Architecture

```
terminal client (python -m client)
    │  WebSocket: chat.send {sessionKey, message, idempotencyKey}
    ▼
gateway (python -m agentd)            ← responds {runId} immediately, runs async
    │  agent loop: LLM → tool calls → execute → results → LLM … until done
    │  + continuation/verification retries (planning/reasoning/empty, verify hook)
    ▼
events streamed back (chat.event)     ← text/thinking deltas, tool activity, lifecycle
transcripts persisted as JSONL        ← v2/.agentd/sessions/<sessionKey>.jsonl
```

Tools: `read` `write` `edit` `exec` `process` `web_search` `web_fetch` `browser`

## Setup (one time)

```powershell
cd v2
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium    # for the browser tool
```

Then create `v2/.env` (copy `.env.example` and fill in a provider key):

```
GEMINI_API_KEY=...            # or ANTHROPIC_API_KEY / OPENAI_API_KEY
AGENTD_MODEL=gemini/gemini-2.5-pro    # any LiteLLM model id
AGENTD_REASONING=medium               # off | low | medium | high (thinking)
BRAVE_API_KEY=...                      # optional; web_search falls back to DuckDuckGo
```

## Run

Use the local venv's python so it stays isolated:

```powershell
# Terminal 1 (gateway)
.\.venv\Scripts\python.exe -X utf8 -m agentd

# Terminal 2 (client)
.\.venv\Scripts\python.exe -X utf8 -m client      # --session <id> to resume
```

Client commands: `/abort` `/new` `/quit`

Tip: `.\.venv\Scripts\Activate.ps1` once, then plain `python -X utf8 -m agentd`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/
```

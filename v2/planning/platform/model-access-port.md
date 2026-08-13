# Model access — ONE funnel, every mode, every tool, forever

Status: **BUILT** (2026-08-13, uncommitted). Diagrams: `diagrams/model-access-gap-and-fix.puml`.
Full unit suite green (1493 passed). One deploy-side verification left (below).

## The rule

**A tool asks for model WORK; the runtime owns transport, credential and billing.**
Tools never import a provider SDK, never read env keys, never branch on deployment mode.

## What a tool writes (the whole contract)

```python
needs_model = True
model_kind  = "text" | "vision" | "image-gen"
model = self.resolve_model(self.config, per_call=params.get("model"))
self.models.text(...) / self.models.vision(...) / self.models.generate_image(...)
```

`self.models` is the **ModelAccess port** (`application/interfaces/model_access.py`), defaulting
to `FunnelModelAccess` (`infrastructure/llm/model_access.py`) — a stateless delegator onto the
**funnel** `infrastructure/llm/oneshot.py`. Tests fake it via `tool._model_access = fake`.

## Where mode-correctness lives (once)

| mode | transport | who pays |
|---|---|---|
| local BYOK | provider direct | the user's own env key (`GEMINI_API_KEY`…) |
| desktop cloud | LiteLLM proxy | the CONNECTION's session token |
| web hosted (forced) | LiteLLM proxy | the server's master key, `user`-attributed |
| Local pin | provider direct | nothing platform-side |
| plugin sandbox | host-brokered RPC | whatever the HOST's turn resolves to |

- Chat shapes: `oneshot.text_complete`/`vision_complete` → LiteLLM → `model_proxy.apply()`.
- Image-out (new): `oneshot.generate_image` — native google-genai `generate_content`
  (inline_data image parts don't fit chat completions), transport from
  `model_proxy.passthrough("gemini")`: proxied turns point the SDK at `<proxy>/gemini`
  (LiteLLM's built-in native passthrough) with the turn's own credential; direct turns use the
  ONE remaining copy of the env-key chain. Per-call `api_key` is a BYOK override only —
  proxied turns and the sandbox ignore it.

## Enforcement (why "never again" is mechanical, not hopeful)

1. **Sandbox** (untrusted = agent-builder-made / marketplace plugins): child env is an
   ALLOWLIST (no keys exist to steal), `sys.modules` pre-seeds a broker-backed `oneshot` stub, and
   the broker checks the grant (models, fs paths, call caps). NEW: `image` kind — reference
   paths AND `out_path` must be inside the grant.
2. **Authoring** (`agent-builder`): `create_tool` derives `needs_model`/`model_kind` from the
   code (now recognises `generate_image`/`self.models.*` → "image-gen") and REFUSES env reads,
   net imports, spawns before the file exists.
3. **CI tripwire**: `tests/unit/test_no_rogue_model_keys.py` — no `genai.Client(` anywhere in
   `v2/plugins`; every key-shaped `os.environ` read must sit in a justified allowlist (which
   also fails when stale, so it can only shrink truthfully).

## What was retrofitted

- `figure-art`: `generate_artwork` + `edit_artwork` → `self.models.generate_image`;
  `figure_art_gemini.py` DELETED (its transport moved into the funnel).
- `vision`: `read_labels_from_image`'s strip step → the port; dead `resolve_key` deleted
  (`verify_figure`/`analyze` were already funnel-routed — they just stop pre-demanding a key,
  which is what threw "no Gemini API key" on hosted).
- `vectorize`: shared `strip_labels` → funnel; dead `vectorize_gemini.py` DELETED.
- Five copy-pasted `resolve_key()` chains → zero (one env fallback lives in the funnel).

## Deliberately out of scope (each with its story)

- **fal/replicate backends**: BYOK-only by design — not proxied, they need the user's own
  FAL_KEY/REPLICATE_API_TOKEN; allowlisted in the tripwire with reasons; tool description says
  so. Route them through LiteLLM image_generation later if hosted demand appears.
- **web-search gemini provider**: reads GEMINI_API_KEY but degrades gracefully (provider simply
  not registered without a key); allowlisted. Candidate for the same passthrough later.
- **Egress allowlist for provider domains** in the sandbox: `domain/sandbox_net.py` exists;
  tightening it to block provider hosts outright is the kernel-grade follow-up.

## One deploy-side verification (user runs)

LiteLLM's `/gemini/*` native passthrough ships with the proxy and uses the proxy's own
GEMINI_API_KEY; auth flows through our `custom_auth` (key arrives as `x-goog-api-key`). After
the next model-proxy deploy, verify once:

```bash
curl -s -X POST "$PROXY/gemini/v1beta/models/gemini-2.5-flash:generateContent" \
  -H "x-goog-api-key: $SESSION_OR_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"say ok"}]}]}'
```

200 ⇒ hosted/desktop-cloud image generation works end to end. If the pinned litellm (1.88.1)
does not meter passthrough spend per key, metering for image calls is the follow-up item —
the calls still authenticate and route correctly either way.

"""THE MODEL FUNNEL — every one-shot model call a tool makes goes through this module.

One module on purpose, and three things hang off that fact:

  * MODE-CORRECTNESS lives here once. Each call consults `model_proxy` — local BYOK goes direct
    on the user's own keys; desktop cloud and web hosted are retargeted at OUR LiteLLM proxy and
    pay with the connection's session token (or the server's master key). No tool ever branches
    on the deployment mode, and no tool ever holds a credential.
  * The SANDBOX swaps this module (sandbox/child_models.py pre-seeds sys.modules), so an
    untrusted plugin calling these same functions is transparently served by the host through
    the model broker — granted, clamped and metered. Same code, both sides of the trust line.
  * New model SHAPES are added HERE (one function + one broker kind), never in a plugin. A
    plugin that builds its own provider client has left the funnel — the guard test
    (tests/unit/test_no_rogue_model_keys.py) fails the build when one tries.

`text_complete`/`vision_complete` route via LiteLLM (any provider). `generate_image` is the
image-bytes-out shape LiteLLM's chat path can't carry: it speaks the native Gemini SDK, but the
TRANSPORT is still decided here — direct in BYOK, the proxy's /gemini passthrough in cloud modes.

Deliberately NARROW: one prompt in, one text/image out, no streaming, no tools — the agent
loop's `litellm_stream` stays the place for conversational calls.

Coordinate-accuracy note: Gemini is uniquely good at spatial grounding (the read_labels_from_image prompt is
written in its native 0-1000 convention). Routing a "gemini/..." model here hits Gemini, so accuracy is
unchanged; pointing grounding at a non-Gemini model is possible but the localization quality is on the
caller — the plumbing is provider-agnostic, the training is not.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path


def normalize_model(model: str) -> str:
    """Make a model id litellm-routable. A bare id (no "provider/") implies gemini — this preserves
    the plugins' historical bare Gemini constants ("gemini-2.5-flash") while letting config pass a
    fully-qualified "openai/gpt-4.1-mini" straight through."""
    m = (model or "").strip()
    return m if "/" in m else f"gemini/{m}"


def _image_part(path: Path) -> dict:
    data = base64.b64encode(path.read_bytes()).decode()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def _meter(resp, model: str) -> None:
    """HOSTED metering: fold a one-shot call's tokens+cost into the current turn's per-account
    accumulator. No-op outside an account-scoped turn (contextvars propagate through to_thread)."""
    try:
        u = getattr(resp, "usage", None)
        if u is not None:
            from agent_runtime.infrastructure import accounts as _accts

            _accts.add_usage(
                model,
                getattr(u, "prompt_tokens", 0) or 0,
                getattr(u, "completion_tokens", 0) or 0,
            )
    except Exception:  # noqa: BLE001 — metering must never break a tool call
        pass


def text_complete(
    *,
    model: str,
    prompt: str,
    max_tokens: int | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
) -> str:
    """One text-only prompt -> the model's text. The non-streaming, no-tools sibling of
    vision_complete, for small server-side utility calls (e.g. auto-titling a chat).
    Synchronous — call it from a worker thread (asyncio.to_thread)."""
    import litellm

    litellm.suppress_debug_info = True
    model = normalize_model(model)
    if not api_key and model.startswith("gemini/"):
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    kwargs: dict = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if api_key:
        kwargs["api_key"] = api_key
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if timeout:
        kwargs["request_timeout"] = timeout

    from agent_runtime.infrastructure.llm import model_proxy

    model_proxy.apply(kwargs)  # platform-keys mode: route via our proxy (no-op if off)
    resp = litellm.completion(**kwargs)
    _meter(resp, model)
    return (resp.choices[0].message.content or "") if resp.choices else ""


def vision_complete(
    *,
    model: str,
    prompt: str,
    image_paths,
    want_json: bool = False,
    api_key: str | None = None,
    timeout: float | None = None,
) -> str:
    """One image+prompt call -> the model's text (or JSON string in JSON mode).

    `model` is a litellm id (bare id => gemini). `image_paths` is one path or a list. `api_key`
    overrides the provider's env key; for a gemini model with no explicit key we fall back to
    GEMINI_API_KEY/GOOGLE_API_KEY so the historical Gemini-key setup keeps working. Synchronous —
    call it from a worker thread (the vision tools already run `_run` via asyncio.to_thread)."""
    import litellm

    litellm.suppress_debug_info = True
    model = normalize_model(model)
    if not api_key and model.startswith("gemini/"):
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    paths = [image_paths] if isinstance(image_paths, (str, Path)) else list(image_paths)
    content = [{"type": "text", "text": prompt}]
    content.extend(_image_part(Path(p)) for p in paths)

    kwargs: dict = {"model": model, "messages": [{"role": "user", "content": content}]}
    if api_key:
        kwargs["api_key"] = api_key
    if want_json:
        # OpenAI-style JSON mode; litellm maps it to each provider's own (Gemini
        # response_mime_type, Anthropic, ...). Providers that lack it degrade to plain text,
        # which the caller's tolerant JSON parse still handles.
        kwargs["response_format"] = {"type": "json_object"}
    if timeout:
        kwargs["request_timeout"] = timeout

    from agent_runtime.infrastructure.llm import model_proxy

    model_proxy.apply(kwargs)  # platform-keys mode: route via our proxy (no-op if off)
    resp = litellm.completion(**kwargs)
    _meter(resp, model)
    return (resp.choices[0].message.content or "") if resp.choices else ""


# --------------------------------------------------------------------------- image generation
# The one call shape LiteLLM's completion path cannot carry: Gemini's image models return the
# picture as an inline_data part on generate_content. The SDK call is native; the ROUTING is not
# the tool's business — it is decided here exactly like apply() decides it for litellm calls.


def _bare_gemini_id(model: str) -> str:
    """The google-genai SDK wants a BARE model id, but the model knob is shared with the
    litellm-based tools and often carries a 'gemini/' (or 'google/'/'models/') prefix. Strip
    repeatedly so 'models/gemini/…' fully unwraps; passing a prefix through 404s."""
    m = str(model or "").strip()
    changed = True
    while changed:
        changed = False
        for pref in ("gemini/", "google/", "models/"):
            if m.startswith(pref):
                m, changed = m[len(pref) :], True
    return m


def _alt_image_model(model: str) -> str | None:
    """A model whose preview/GA id drifted: the -preview variant (or drop it). None if no alt."""
    if model.endswith("-preview"):
        return model[: -len("-preview")]
    return model + "-preview"


def _is_not_found(err: Exception) -> bool:
    s = str(err)
    return "404" in s or "not found" in s.lower() or "NOT_FOUND" in s.upper()


def _image_gen_client(api_key: str | None, timeout: float | None):
    """A google-genai Client whose TRANSPORT matches this turn's mode — the native-SDK twin of
    `model_proxy.apply()`. Proxied turns hit the proxy's /gemini passthrough and pay with the
    turn's own credential (session token, or the server's master key); the proxy meters them per
    account and holds the real provider key. Direct (BYOK) turns use the caller's override or the
    user's own env key — the ONE place that fallback chain exists."""
    from google import genai
    from google.genai import types

    from agent_runtime.infrastructure import telemetry
    from agent_runtime.infrastructure.llm import model_proxy

    http_kwargs: dict = {}
    if timeout:
        http_kwargs["timeout"] = int(float(timeout) * 1000)  # HttpOptions wants milliseconds

    base_url, turn_key = model_proxy.passthrough("gemini")
    if base_url:
        trace = telemetry.trace_headers()
        if trace:
            http_kwargs["headers"] = trace  # correlation across the process boundary, as apply() does
        return genai.Client(
            api_key=turn_key, http_options=types.HttpOptions(base_url=base_url, **http_kwargs)
        )

    key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError(
            "no Gemini API key (set GEMINI_API_KEY or GOOGLE_API_KEY, or sign in so the "
            "platform's model proxy can carry the call)"
        )
    return genai.Client(
        api_key=key, http_options=types.HttpOptions(**http_kwargs) if http_kwargs else None
    )


def _reference_parts(reference_images):
    from google.genai import types

    parts = []
    for p in reference_images or []:
        path = Path(p)
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=mime))
    return parts


def generate_image(
    *,
    model: str,
    prompt: str,
    out_path,
    reference_images=None,
    aspect_ratio: str | None = None,
    image_size: str | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
) -> dict:
    """One prompt (+ optional reference-image conditioning) -> ONE image written to `out_path`.

    `model` may be a litellm-style id ('gemini/gemini-3-pro-image') or bare — normalized here.
    `reference_images` (paths) condition layout/style (Gemini's restyle/img2img path).
    `aspect_ratio` ('4:3') and `image_size` ('1K'|'2K'|'4K') apply best-effort so an older
    SDK/model that lacks a field degrades instead of failing. `api_key` is a per-call BYOK
    override only — proxied turns ignore it (the turn's own credential pays, always).
    Returns {path, mime, model}. Synchronous — call from a worker thread."""
    from google.genai import types

    out_path = Path(out_path)
    client = _image_gen_client(api_key, timeout)
    contents = _reference_parts(reference_images) + [prompt]
    model = _bare_gemini_id(model)

    cfg = None
    if aspect_ratio or image_size:
        # image_config fields applied best-effort: an unsupported one never fails the call
        # (drop it and retry with what remains).
        img_kw = {}
        if aspect_ratio:
            img_kw["aspect_ratio"] = aspect_ratio
        if image_size:
            img_kw["image_size"] = image_size
        for attempt in (img_kw, {"aspect_ratio": aspect_ratio} if aspect_ratio else {}, {}):
            try:
                cfg = (
                    types.GenerateContentConfig(image_config=types.ImageConfig(**attempt))
                    if attempt
                    else None
                )
                break
            except Exception:  # noqa: BLE001 — degrade the knob, never the generation
                cfg = None

    # Tolerate a preview/GA id drift: a NotFound retries once on the -preview (or GA) variant.
    try:
        resp = client.models.generate_content(model=model, contents=contents, config=cfg)
    except Exception as e:
        alt = _alt_image_model(model)
        if not (_is_not_found(e) and alt):
            raise
        resp = client.models.generate_content(model=alt, contents=contents, config=cfg)
        model = alt

    for cand in resp.candidates or []:
        for part in getattr(cand.content, "parts", None) or []:
            blob = getattr(part, "inline_data", None)
            if blob and getattr(blob, "data", None):
                data = blob.data
                if isinstance(data, str):  # some SDK paths hand back base64 text
                    data = base64.b64decode(data)
                out_path.write_bytes(data)
                return {
                    "path": str(out_path),
                    "mime": blob.mime_type or "image/png",
                    "model": model,
                }
    # no image -> surface any text the model returned instead (often a refusal/explanation)
    txt = getattr(resp, "text", "") or "(no image and no text in response)"
    raise RuntimeError(f"model returned no image: {txt[:300]}")

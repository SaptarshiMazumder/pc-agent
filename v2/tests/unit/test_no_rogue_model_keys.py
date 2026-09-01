"""THE TRIPWIRE: no plugin builds its own model transport or reads provider keys from env.

The rule this enforces (see oneshot's module docstring — the model FUNNEL): a tool asks the
runtime for model work via ``self.models`` / ``oneshot.*``; the runtime owns transport, credential
and billing per deployment mode. A plugin that constructs a provider client or reads a key-shaped
env var has left the funnel — it will look correct on the author's desktop (key in env) and be
broken or unmetered everywhere else. That is exactly how the original figure-art/vision/vectorize
plugins failed on web-hosted, and this test is what makes the fix permanent instead of a cleanup.

Adding a NEW hit fails this test by design. The fix is almost never the allowlist:
  * model call            -> self.models.text/vision/generate_image (mode-correct + sandbox-safe)
  * outbound API call     -> agent_runtime.infrastructure.net.outbound.fetch + [sandbox] net hosts
  * genuinely BYOK-only
    provider backend      -> allowlist below, WITH the reason, and the tool's description must
                             say it needs the user's own key (hosted refuses it cleanly).
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGINS = Path(__file__).resolve().parents[2] / "plugins"

#: Building a provider SDK client in a plugin. No allowlist: the funnel is the only transport.
PROVIDER_CLIENT = re.compile(r"\bgenai\.Client\s*\(")

#: Reading (or writing) a key-shaped env var. os.environ.get("X_API_KEY") / os.environ["FAL_KEY"].
ENV_KEY = re.compile(
    r"os\.(?:environ(?:\.get)?|getenv)\s*[\(\[]\s*[\"']"
    r"([A-Z0-9_]*(?:API_KEY|API_TOKEN|_KEY|_TOKEN|_SECRET|SECRET_KEY)[A-Z0-9_]*)"
)

#: Every allowed (file, env var) pair, each with its reason. An entry that stops matching goes
#: stale and fails the test too — the list can only shrink truthfully.
ALLOWED_ENV_KEYS: dict[tuple[str, str], str] = {
    ("figure-art/figure_art_flux.py", "FAL_KEY"): "BYOK-only backend: fal.ai is not proxied; needs the user's own key",
    ("figure-art/figure_art_flux.py", "FALAI_KEY"): "BYOK-only backend: legacy alias of FAL_KEY",
    ("figure-art/figure_art_replicate.py", "REPLICATE_API_TOKEN"): "BYOK-only backend: replicate is not proxied",
    ("figure-art/figure_art_replicate.py", "REPLICATE_API_KEY"): "BYOK-only backend: legacy alias",
    ("web/search/providers/gemini.py", "GEMINI_API_KEY"): "optional BYOK search grounding; provider is skipped when unset",
    ("web/search/factory.py", "GEMINI_API_KEY"): "gates REGISTERING the gemini search provider on a key existing",
}


def _hits(pattern: re.Pattern) -> list[tuple[str, str]]:
    out = []
    for path in sorted(PLUGINS.rglob("*.py")):
        rel = path.relative_to(PLUGINS).as_posix()
        for m in pattern.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            out.append((rel, m.group(1) if m.groups() else m.group(0)))
    return out


def test_no_plugin_constructs_a_provider_client():
    assert _hits(PROVIDER_CLIENT) == [], (
        "a plugin is building its own google-genai client. Model calls go through the funnel "
        "(self.models / oneshot.generate_image) so BYOK/cloud/sandbox all route and meter "
        "correctly — see oneshot's module docstring."
    )


def test_every_key_shaped_env_read_in_a_plugin_is_explicitly_justified():
    unexplained = [hit for hit in _hits(ENV_KEY) if hit not in ALLOWED_ENV_KEYS]
    assert unexplained == [], (
        f"plugin code reads key-shaped env vars with no recorded justification: {unexplained}. "
        "Use the model funnel (self.models) or the brokered fetch (${NAME} secrets) instead; a "
        "genuinely BYOK-only provider backend gets an ALLOWED_ENV_KEYS entry with its reason."
    )


def test_the_allowlist_carries_no_stale_entries():
    live = set(_hits(ENV_KEY))
    stale = [entry for entry in ALLOWED_ENV_KEYS if entry not in live]
    assert stale == [], f"ALLOWED_ENV_KEYS entries no longer present in code: {stale}"

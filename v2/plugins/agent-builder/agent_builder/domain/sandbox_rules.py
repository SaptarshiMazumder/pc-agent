"""SandboxRules — will this agent's OWN tools still work once they are treated as untrusted?

Pure rules, no I/O — the caller passes each private plugin's source text in.

WHY THIS EXISTS
``classify_origin()`` decides trust by LOCATION, not authorship: any tool discovered under
``agents/<id>/plugins/`` is stamped ``_agent_id`` and classified THIRD_PARTY_BUNDLE — untrusted.
The reasoning is sound: an agent can be DOWNLOADED as a .agentpkg carrying someone else's Python,
which would then run on your machine with your keys. The classifier cannot tell "I authored this
by chat" from "I installed this from the store", so it judges the folder.

That folder is exactly where `create_tool(agent=...)` writes. So a tool you author for your own
agent joins the untrusted tier, and ``DefaultCapabilityResolver`` would hand it:
    fs      -> the run's workspace only
    net     -> nothing
    secrets -> {}   ALWAYS. It never sees a provider key.

ADVISORY, NOT A GATE — deliberately.
Today ``sandbox_untrusted_plugins`` defaults OFF, and even when ON the shipped backend
(``LocalPluginSandbox``) is an in-process passthrough that does not enforce the grant at all.
So nothing here is broken right now, and these findings are INFO/WARN — never ERROR. They exist
so an author learns at WRITING time that a tool reaching for an API key or the network is living
on borrowed time: it breaks the day a real isolating backend lands, and it breaks on the machine
of anyone who installs the agent.
"""

from __future__ import annotations

import re

from .finding import INFO, WARN, Finding

# Signals that a private plugin wants something the untrusted grant denies. Deliberately a
# coarse source scan: the point is to prompt a human decision, not to prove anything.
SECRET_PATTERNS = (
    re.compile(r"os\.environ|getenv|environb", re.I),
    re.compile(r"[A-Z0-9_]*(API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*"),
)
NETWORK_PATTERNS = (
    re.compile(r"\b(?:import|from)\s+(?:httpx|requests|aiohttp|urllib|http\.client|socket)\b"),
    re.compile(r"https?://"),
)


class SandboxRules:
    """Forward-looking checks over the agent's OWN plugins/ tier."""

    name = "sandbox"

    def check(self, spec, raw_toml: dict, files: list[str], sources: dict) -> list[Finding]:
        """`sources` maps a plugins/<pid>/<module>.py path -> its text (already read by infra)."""
        plugin_ids = sorted(
            {
                rel.split("/")[1]
                for rel in files
                if rel.startswith("plugins/") and len(rel.split("/")) >= 3
            }
        )
        if not plugin_ids:
            return []

        out: list[Finding] = [
            Finding(
                level=INFO,
                code="PRIVATE_TOOLS_UNTRUSTED",
                message=(
                    f"this agent ships {len(plugin_ids)} private plugin(s) "
                    f"({', '.join(plugin_ids)}). Tools under agents/<id>/plugins/ are classified "
                    f"THIRD_PARTY_BUNDLE (untrusted) — they are sandboxed when "
                    f"AGENTD_SANDBOX_PLUGINS is on, and always are on someone else's machine"
                ),
                path="plugins/",
            )
        ]
        for pid in plugin_ids:
            text = "\n".join(
                src for rel, src in sources.items() if rel.startswith(f"plugins/{pid}/")
            )
            if not text:
                continue
            if any(p.search(text) for p in SECRET_PATTERNS):
                out.append(
                    Finding(
                        level=WARN,
                        code="UNTRUSTED_WANTS_SECRETS",
                        message=f"plugins/{pid}/ reads environment variables / API keys, but an "
                        f"untrusted tool is granted secrets = {{}} — it will read nothing",
                        path=f"plugins/{pid}/",
                        fix=f"take the value as a tool PARAMETER, or exempt it locally via "
                        f"config sandbox_trusted_plugins = [\"{pid}\"] (only for code you wrote)",
                    )
                )
            if any(p.search(text) for p in NETWORK_PATTERNS):
                out.append(
                    Finding(
                        level=WARN,
                        code="UNTRUSTED_WANTS_NETWORK",
                        message=f"plugins/{pid}/ makes network calls, but an untrusted tool is "
                        f"granted no network access",
                        path=f"plugins/{pid}/",
                        fix=f"use a shared tool that already owns the network path, or exempt it "
                        f'via config sandbox_trusted_plugins = ["{pid}"]',
                    )
                )
        return out

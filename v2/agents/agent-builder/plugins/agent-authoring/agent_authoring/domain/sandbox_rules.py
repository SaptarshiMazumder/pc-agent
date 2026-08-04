"""SandboxRules — will this agent's OWN tools still work once they are treated as untrusted?

Pure rules, no I/O — the caller passes each private plugin's source text in.

WHY THIS EXISTS — AND WHY IT IS ABOUT *SHIPPING*, NOT ABOUT HERE
``classify_origin()`` decides trust by PROVENANCE: an agent's private tools are untrusted when
the marketplace ledger says that agent arrived in a ``.agentpkg``. So on THIS machine, an agent
you authored (or one that shipped with the product) keeps its tools trusted — owning tools is
not itself suspicious.

The catch is what happens NEXT. The moment someone installs your agent, it lands in THEIR ledger,
and every tool in ``agents/<id>/plugins/`` becomes THIRD_PARTY_BUNDLE over there. That is exactly
the folder ``create_tool(agent=...)`` writes to. ``DefaultCapabilityResolver`` then hands it:
    fs      -> the run's workspace only
    net     -> nothing
    secrets -> {}   ALWAYS. It never sees a provider key.

So these checks answer one question: *will this agent still work once someone else installs it?*
A tool that reads an API key runs fine for its author and silently reads nothing for everybody
who downloads it — the worst possible failure shape, and invisible without this warning.

ADVISORY, NOT A GATE — deliberately.
``sandbox_untrusted_plugins`` defaults OFF, and even when ON the shipped backend
(``LocalPluginSandbox``) is an in-process passthrough that does not enforce the grant. Nothing
here is broken today, so every finding is INFO/WARN — never ERROR. They exist so an author learns
at WRITING time that such a tool is living on borrowed time: it breaks the day a real isolating
backend lands, and it breaks on the machine of anyone who installs the agent.
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
                    f"({', '.join(plugin_ids)}). They are trusted HERE (this agent was not "
                    f"installed from a package), but once someone else installs this agent its "
                    f"ledger marks it THIRD_PARTY_BUNDLE and these tools get sandboxed"
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
                        message=f"plugins/{pid}/ reads environment variables / API keys. That "
                        f"works for you, but an installed agent is granted secrets = {{}} — for "
                        f"everyone who downloads this, it will read nothing",
                        path=f"plugins/{pid}/",
                        fix=f"take the value as a tool PARAMETER, or have whoever installs it "
                        f'vouch for the agent via config sandbox_trusted_agents = ["{spec.id}"]',
                    )
                )
            if any(p.search(text) for p in NETWORK_PATTERNS):
                out.append(
                    Finding(
                        level=WARN,
                        code="UNTRUSTED_WANTS_NETWORK",
                        message=f"plugins/{pid}/ makes network calls. That works for you, but an "
                        f"installed agent is granted no network access",
                        path=f"plugins/{pid}/",
                        fix=f"use a shared tool that already owns the network path, or have "
                        f'whoever installs it set sandbox_trusted_agents = ["{spec.id}"]',
                    )
                )
        return out

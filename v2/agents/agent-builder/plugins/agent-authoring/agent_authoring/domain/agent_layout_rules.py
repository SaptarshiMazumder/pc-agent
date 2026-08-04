"""AgentLayoutRules — is this agent directory BUILT correctly?

Pure rules, no I/O. The caller hands over what it already read; these functions only judge.

These rules exist for one specific class of bug: **things the daemon tolerates but the author
did not intend**. The parser answers "does it load"; it says nothing about whether the author's
words are actually being used.

The motivating real case: `expense-summarizer` declared `color`, `tagline`, `suggestions` and
`description` INSIDE `[app]`. Valid TOML. Loads fine. But the registry reads those keys from the
TOP level (`data.get(...)`) and falls back to the generated presentation.json sidecar — so the
authored green `#2E7D32` was silently replaced by a generated purple, and the authored prompts by
generated ones. Nothing errored, and nobody noticed. That is exactly what a layout rule catches.
"""

from __future__ import annotations

from .finding import ERROR, WARN, Finding

# The ONLY keys agent.toml's [app] table is read for (file_registry._load_dir), plus `icon`,
# which the INSTALLER build reads (gen-app-flavor.mjs) though the daemon ignores it.
APP_TABLE_KEYS = frozenset({"entry", "title", "mode", "public", "public_tools", "icon"})

# Display fields that MUST be top-level. Nested under [app] they are silently dropped.
TOP_LEVEL_DISPLAY_KEYS = frozenset({"tagline", "color", "suggestions", "description"})

VALID_APP_MODES = frozenset({"window", "browser"})


class AgentLayoutRules:
    """Structural + authoring-intent checks over one agent directory."""

    name = "layout"

    def check(self, spec, raw_toml: dict, files: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        findings += self._identity(files)
        findings += self._app_table(raw_toml)
        findings += self._skills(files)
        findings += self._private_plugins(files)
        return findings

    # ---------------------------------------------------------------- identity
    def _identity(self, files: list[str]) -> list[Finding]:
        if "IDENTITY.md" in files:
            return []
        return [
            Finding(
                level=ERROR,
                code="NO_IDENTITY",
                message="no IDENTITY.md — the agent has no persona, so it falls back to a bare default",
                path="IDENTITY.md",
                fix="write IDENTITY.md: who this agent is, its role, tone and boundaries",
            )
        ]

    # ---------------------------------------------------------------- [app]
    def _app_table(self, raw_toml: dict) -> list[Finding]:
        app = raw_toml.get("app")
        if not isinstance(app, dict):
            return []
        out: list[Finding] = []
        for key in sorted(set(app) - APP_TABLE_KEYS):
            if key in TOP_LEVEL_DISPLAY_KEYS:
                out.append(
                    Finding(
                        level=WARN,
                        code="APP_TABLE_STRAY_DISPLAY_KEY",
                        message=(
                            f"`{key}` is inside [app], where nothing reads it — it is SILENTLY "
                            f"IGNORED and the generated presentation.json value is used instead"
                        ),
                        path="agent.toml",
                        fix=f"move `{key}` above the [app] line so it is a top-level key",
                    )
                )
            else:
                out.append(
                    Finding(
                        level=WARN,
                        code="APP_TABLE_UNKNOWN_KEY",
                        message=f"[app] has no `{key}` key — it is read for "
                        f"{', '.join(sorted(APP_TABLE_KEYS))} only, so this has no effect",
                        path="agent.toml",
                    )
                )
        mode = str(app.get("mode") or "").strip().lower()
        if mode and mode not in VALID_APP_MODES:
            out.append(
                Finding(
                    level=WARN,
                    code="APP_BAD_MODE",
                    message=f"[app] mode = '{mode}' is not recognised; it falls back to 'browser'",
                    path="agent.toml",
                    fix="use mode = \"window\" (its own chromeless window) or \"browser\" (a tab)",
                )
            )
        return out

    # ---------------------------------------------------------------- skills/
    def _skills(self, files: list[str]) -> list[Finding]:
        """Every skills/<name>/ must hold a SKILL.md — a folder without one is invisible."""
        named: dict[str, list[str]] = {}
        for rel in files:
            parts = rel.split("/")
            if len(parts) >= 3 and parts[0] == "skills":
                named.setdefault(parts[1], []).append("/".join(parts[2:]))
        return [
            Finding(
                level=ERROR,
                code="SKILL_NO_MD",
                message=f"skills/{skill}/ has no SKILL.md — the skill loader will not see it",
                path=f"skills/{skill}/",
                fix=f"write skills/{skill}/SKILL.md with `name:` and `description:` frontmatter",
            )
            for skill, contents in sorted(named.items())
            if "SKILL.md" not in contents
        ]

    # ---------------------------------------------------------------- plugins/
    def _private_plugins(self, files: list[str]) -> list[Finding]:
        """Every plugins/<pid>/ needs plugin.toml; a native one needs its entry module present."""
        by_plugin: dict[str, list[str]] = {}
        for rel in files:
            parts = rel.split("/")
            if len(parts) >= 3 and parts[0] == "plugins":
                by_plugin.setdefault(parts[1], []).append("/".join(parts[2:]))
        out: list[Finding] = []
        for pid, contents in sorted(by_plugin.items()):
            if "plugin.toml" not in contents:
                out.append(
                    Finding(
                        level=ERROR,
                        code="PLUGIN_NO_MANIFEST",
                        message=f"plugins/{pid}/ has no plugin.toml — the plugin is never loaded",
                        path=f"plugins/{pid}/",
                        fix='write plugin.toml with id, name, kind = "native", entry = "<module>:register"',
                    )
                )
                continue
            if not any(c.endswith(".py") for c in contents):
                out.append(
                    Finding(
                        level=ERROR,
                        code="PLUGIN_NO_MODULE",
                        message=f"plugins/{pid}/ declares a manifest but ships no .py module",
                        path=f"plugins/{pid}/",
                        fix="write the module named by `entry`, exposing register(api, ctx)",
                    )
                )
        return out

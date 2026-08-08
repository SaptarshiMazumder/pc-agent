"""PackageabilityRules — will this agent survive the trip to a shippable .exe?

Pure rules, no I/O. Everything here is something that loads FINE on the authoring machine and
only bites later, in the installer pipeline:

    agentd bundle pack  ->  npm run gen:app  ->  npm run dist:app  ->  "<Name> Setup <ver>.exe"

Each check mirrors a real gate in that pipeline rather than inventing policy:
  * gen-app-flavor.mjs REJECTS an agent with no [app] section.
  * gen-app-flavor.mjs reads `version` (defaulting 1.0.0) and installs supersede BY VERSION.
  * the daemon serves [app] entry off disk; a missing entry file is a 404 at launch.
  * bundle_io.EXCLUDED_DIRS drops workspace/ and clients/ from the package entirely — anything
    the agent NEEDS that lives there is present locally and absent for every recipient.

Mirrored constants are duplicated deliberately (the rule must state what it checks), but they are
verified against the real ones by the bundle's tests, so drift is caught rather than assumed.
"""

from __future__ import annotations

from .finding import ERROR, INFO, WARN, Finding

# Mirrors agent_runtime.infrastructure.marketplace.bundle_io.EXCLUDED_DIRS.
EXCLUDED_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".pytest_cache",
        "node_modules",
        "workspace",
        "sessions",
        ".agentd",
        "clients",
    }
)

# Directories whose contents are part of the agent's DEFINITION — finding these nested under an
# excluded dir means the author put load-bearing files somewhere that never ships.
DEFINITION_DIRS = frozenset({"skills", "plugins", "ui", "templates"})


class PackageabilityRules:
    """Checks that only fail LATER — at pack/build/install time — unless caught here."""

    name = "packageability"

    def check(self, spec, raw_toml: dict, files: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        findings += self._app_and_version(spec, raw_toml, files)
        findings += self._excluded_dirs(files)
        return findings

    # ------------------------------------------------------- product-ability
    def _app_and_version(self, spec, raw_toml: dict, files: list[str]) -> list[Finding]:
        out: list[Finding] = []
        app = raw_toml.get("app")
        has_app = isinstance(app, dict)

        if not has_app:
            # A ui/ with no [app] is never intentional: somebody built the interface and the
            # declaration that serves it is gone. It is what a re-scaffold leaves behind when
            # it rewrites agent.toml from the skeleton, and it is invisible otherwise — the
            # files are all still there, the window just never opens. ERROR, not info.
            orphaned = [f for f in files if f.startswith("ui/")]
            if orphaned:
                out.append(
                    Finding(
                        level=ERROR,
                        code="ORPHANED_UI",
                        message=f"ui/ exists ({len(orphaned)} file(s)) but agent.toml has NO "
                        f"[app] section — nothing serves this interface, so the window can "
                        f"never open. Usually means [app] was wiped by a re-scaffold",
                        path="agent.toml",
                        fix="restore the [app] table (title, mode, entry = 'ui/index.html'), "
                        "or delete ui/ if the agent is meant to be chat-only",
                    )
                )
            else:
                out.append(
                    Finding(
                        level=INFO,
                        code="NOT_A_PRODUCT",
                        message="no [app] section — this is a chat-only agent and cannot be "
                        "built into its own .exe (gen-app-flavor rejects agents without [app])",
                        path="agent.toml",
                        fix="add an [app] table with title + mode, and a ui/, to make it a "
                        "product",
                    )
                )
        else:
            entry = str(app.get("entry") or "ui/index.html")
            if entry not in files:
                out.append(
                    Finding(
                        level=ERROR,
                        code="APP_ENTRY_MISSING",
                        message=f"[app] entry '{entry}' does not exist — the app window will 404",
                        path=entry,
                        fix=f"write {entry}, or point `entry` at the file you did write",
                    )
                )
            if not any(f.startswith("ui/vendor/agentd-client.js") for f in files) and entry.startswith("ui/"):
                out.append(
                    Finding(
                        level=WARN,
                        code="UI_NO_SDK",
                        message="ui/ ships no vendor/agentd-client.js — the page cannot talk to "
                        "the daemon without the SDK",
                        path="ui/vendor/agentd-client.js",
                        fix="copy it from another agent's ui/vendor/ (it is a prebuilt IIFE bundle)",
                    )
                )

        # `version` matters even for a chat-only agent: bundle installs supersede BY version.
        if not str(raw_toml.get("version") or "").strip():
            out.append(
                Finding(
                    level=WARN,
                    code="NO_VERSION",
                    message="no `version` — a reinstall cannot supersede an older copy of this agent",
                    path="agent.toml",
                    fix='add a top-level version = "1.0.0" and bump it on every shipped change',
                )
            )
        return out

    # ------------------------------------------------------- excluded dirs
    def _excluded_dirs(self, files: list[str]) -> list[Finding]:
        """Definition files nested under a dir the packer drops never reach a recipient."""
        seen: set[tuple[str, str]] = set()
        for rel in files:
            parts = rel.split("/")
            for i, part in enumerate(parts[:-1]):
                if part in EXCLUDED_DIRS and any(p in DEFINITION_DIRS for p in parts[i + 1 :]):
                    seen.add((part, "/".join(parts[: i + 2])))
        return [
            Finding(
                level=WARN,
                code="DEFINITION_IN_EXCLUDED_DIR",
                message=f"'{where}' sits under {excluded}/, which the packer EXCLUDES — it works "
                f"here but ships to nobody",
                path=where,
                fix=f"move it out of {excluded}/ into the agent's own definition tree",
            )
            for excluded, where in sorted(seen)
        ]

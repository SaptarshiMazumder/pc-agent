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
from .ui_rules import is_built_app

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
        findings += self._built_app(files)
        findings += self._excluded_dirs(files)
        findings += self._workspace_contents(files)
        return findings

    # ------------------------------------------------------- built apps
    def _built_app(self, files: list[str]) -> list[Finding]:
        """An app compiled from ``app/`` whose ``ui/`` is missing.

        ONLY ``ui/`` SHIPS. `app/` is source; the packer takes what is on disk and the daemon
        serves the build. So sources with no output is an agent that installs and opens a window
        onto nothing — and it is invisible to every other check here, because every file the
        author wrote is present and correct.

        The STALER case — sources newer than the build — needs mtimes, which this rule set does
        not have (it sees a list of names). `verify_app` refuses on it, and that is the right
        home: it is the tool that would otherwise report success about the previous build.
        """
        if not is_built_app(files):
            return []
        if any(f.startswith("ui/") for f in files):
            return []
        return [
            Finding(
                level=ERROR,
                code="UI_NOT_BUILT",
                message="app/ holds the source of a built app but there is no ui/ — `app/` never "
                "ships, so this agent installs with no window at all",
                path="app/",
                fix="cd app && npm install && npm run build (it writes ../ui), and rebuild after "
                "every source change — the daemon serves ui/, not app/",
            )
        ]

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
                        fix="add an [app] table with title + mode, and a ui/, to make it a product",
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
            # A BUILT app has the SDK inside its bundle — it imports `@agentd/client` and the
            # bundler inlines it. Asking for a vendored copy there is worse than useless: the file
            # would live in the bundler's output directory, which the next build empties.
            vendored = any(f.startswith("ui/vendor/agentd-client.js") for f in files)
            if not vendored and entry.startswith("ui/") and not is_built_app(files):
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

    # ------------------------------------------------------- workspace contents
    def _workspace_contents(self, files: list[str]) -> list[Finding]:
        """Files in workspace/ exist HERE and nowhere the agent is going: the packer excludes
        the directory, and on a hosted daemon every user gets their OWN workspace, empty. An
        author who parked templates or seed data there built an agent that only works on this
        machine. INFO, not warn — a working workspace full of the author's own test output is
        normal; the finding exists so 'my shipped agent can't find its files' is diagnosed at
        authoring time instead of by a buyer."""
        count = sum(1 for f in files if f.startswith("workspace/"))
        if not count:
            return []
        return [
            Finding(
                level=INFO,
                code="WORKSPACE_NOT_SHIPPED",
                message=f"workspace/ holds {count} file(s) — none of them ship (the packer "
                f"excludes workspace/), and on the hosted web every user starts with an "
                f"EMPTY workspace of their own",
                path="workspace/",
                fix="anything the agent NEEDS at runtime belongs in a definition dir "
                "(templates/, data/, skills/…), read from there in place",
            )
        ]

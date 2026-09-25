"""PortabilityRules — will this agent still behave the moment it leaves your machine?

Pure rules, no I/O. Everything here is fine ON THE AUTHOR'S DESKTOP and wrong somewhere the
agent is going: a hosted daemon (where the tenant fence scopes reads and every user's
workspace starts empty) or a buyer's install (where the runtime clamps an
installed agent's write scope to its own folder). Each check names the exact deployment
fact it mirrors rather than inventing policy — the runtime refusal exists either way; the
finding exists so the author hears about it while it is still a one-line fix.

Severity here is what the CHECK knows (advisory on this machine). What each code weighs at
the pack/publish gates is the RULEBOOK's decision (domain/rulebook.py), not this module's.
"""

from __future__ import annotations

from .exec_grant import ExecGrant
from .finding import WARN, Finding

#: The file an agent lists what its commands leave behind in (the runtime's AgentdIgnore).
IGNORE_FILE = ".agentdignore"


class PortabilityRules:
    """Declarations that contradict where [delivery] or the marketplace says this agent goes."""

    name = "portability"

    def check(self, spec, raw_toml: dict, files: list[str]) -> list[Finding]:
        raw = raw_toml if isinstance(raw_toml, dict) else {}
        out: list[Finding] = []
        out += self._write_roots(raw)
        out += self._web_delivery(raw)
        out += self._heartbeat(raw)
        out += self._ignore_file(raw, files)
        return out

    # ------------------------------------------------------------- write scope
    def _write_roots(self, raw: dict) -> list[Finding]:
        tools = raw.get("tools")
        fs = tools.get("fs") if isinstance(tools, dict) else None
        roots = fs.get("write_roots") if isinstance(fs, dict) else None
        if not isinstance(roots, list):
            return []
        wide = [str(r) for r in roots if not str(r).strip().startswith("<agent_dir>")]
        if not wide:
            return []
        return [
            Finding(
                level=WARN,
                code="WIDE_WRITE_ROOTS",
                message=f"[tools.fs] write_roots grants writes beyond this agent's own folder "
                f"({', '.join(wide)}) — builder-grade reach. On any machine that installs "
                f"this agent the runtime clamps the scope to its own folder, so the grant "
                f"either does nothing there or should not ship at all",
                path="agent.toml",
                fix="scope write_roots to <agent_dir> (or drop the table) — an agent that "
                "genuinely authors other agents is a local tool, not a marketplace artifact",
            )
        ]

    # ------------------------------------------------------------- web delivery
    def _web_delivery(self, raw: dict) -> list[Finding]:
        delivery = raw.get("delivery")
        if not (isinstance(delivery, dict) and bool(delivery.get("web"))):
            return []
        out: list[Finding] = []
        if bool(raw.get("requires_local")):
            out.append(
                Finding(
                    level=WARN,
                    code="WEB_REQUIRES_LOCAL",
                    message="[delivery] web = true AND requires_local = true — a requires_local "
                    "agent is withheld from hosted daemons entirely (not listed, not served), "
                    "so the web delivery this promises cannot happen",
                    path="agent.toml",
                    fix="drop one of the two: requires_local if the agent can live without its "
                    "local-only tools, or web = true if it cannot",
                )
            )
        return out

    # ------------------------------------------------------------- autonomy pairing
    def _heartbeat(self, raw: dict) -> list[Finding]:
        if not str(raw.get("heartbeat") or "").strip():
            return []
        caps = raw.get("capabilities")
        if isinstance(caps, dict) and bool(caps.get("autonomy")):
            return []
        return [
            Finding(
                level=WARN,
                code="HEARTBEAT_WITHOUT_AUTONOMY",
                message="a `heartbeat` interval is set but [capabilities] autonomy is not true — "
                "the heartbeat never fires, and nothing else says so",
                path="agent.toml",
                fix="add [capabilities] with autonomy = true, or remove `heartbeat`",
            )
        ]

    # ------------------------------------------------------------- what commands leave behind
    def _ignore_file(self, raw: dict, files: list[str]) -> list[Finding]:
        """An agent that runs commands, with no `.agentdignore`.

        Hosted, the workspace is copied into the command sandbox before every `exec` and the
        changes are copied back after; packaged, the whole folder ships. What a command leaves
        behind (a virtualenv, `node_modules/`, a provider cache) is dead weight on both trips
        and can carry the author's own state into a buyer's install. Only the author knows what
        their commands produce, so the file is theirs to keep — this check makes sure it exists."""
        if not ExecGrant.granted(raw) or IGNORE_FILE in files:
            return []
        return [
            Finding(
                level=WARN,
                code="EXEC_WITHOUT_IGNORE_FILE",
                message="this agent can run commands (`exec`) but has no `.agentdignore` — "
                "whatever its commands download or build (virtualenvs, node_modules, tool "
                "caches, state files) is copied through the sandbox on every command and "
                "would ship in its package",
                path=IGNORE_FILE,
                fix="write `.agentdignore` beside agent.toml (gitignore syntax: `name/` for a "
                "folder at any depth, `*.ext` for a file name) listing what the agent's "
                "commands create that must never travel",
            )
        ]

"""PortabilityRules — will this agent still behave the moment it leaves your machine?

Pure rules, no I/O. Everything here is fine ON THE AUTHOR'S DESKTOP and wrong somewhere the
agent is going: a hosted daemon (where the tenant fence scopes reads, exec is refused, and
every user's workspace starts empty) or a buyer's install (where the runtime clamps an
installed agent's write scope to its own folder). Each check names the exact deployment
fact it mirrors rather than inventing policy — the runtime refusal exists either way; the
finding exists so the author hears about it while it is still a one-line fix.

Severity here is what the CHECK knows (advisory on this machine). What each code weighs at
the pack/publish gates is the RULEBOOK's decision (domain/rulebook.py), not this module's.
"""

from __future__ import annotations

from .finding import WARN, Finding


class PortabilityRules:
    """Declarations that contradict where [delivery] or the marketplace says this agent goes."""

    name = "portability"

    def check(self, spec, raw_toml: dict, files: list[str]) -> list[Finding]:
        raw = raw_toml if isinstance(raw_toml, dict) else {}
        out: list[Finding] = []
        out += self._write_roots(raw)
        out += self._web_delivery(raw)
        out += self._heartbeat(raw)
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
        tools = raw.get("tools")
        allow = tools.get("allow") if isinstance(tools, dict) else None
        granted = (
            {str(t).strip() for t in allow if str(t).strip()} if isinstance(allow, list) else set()
        )
        shell = sorted(granted & {"exec", "process"})
        if shell:
            out.append(
                Finding(
                    level=WARN,
                    code="EXEC_ON_WEB",
                    message=f"[delivery] web = true, but [tools] allow grants {', '.join(shell)} — "
                    f"every hosted run refuses the shell (a subprocess cannot be confined to "
                    f"one tenant's files), so the web users this delivery is FOR get an agent "
                    f"whose granted tools error",
                    path="agent.toml",
                    fix="design the agent around read/write/edit/ls/find + plugin tools; if it "
                    "genuinely needs a shell, set requires_local = true and drop web = true",
                )
            )
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

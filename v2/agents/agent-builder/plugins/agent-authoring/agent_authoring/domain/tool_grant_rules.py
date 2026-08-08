"""ToolGrantRules — was this agent given HALF of a tool?

Pure rules, no I/O. The caller hands over the parsed agent.toml; this only judges.

Some tools are a pair. `exec` starts a long job with ``background=true`` and hands back a
session id; `process` is the only way to poll that session, read its output, or kill it. Its
own description says so — *"Use the process tool to poll it."* Grant one without the other and
the model is pointed at a tool it does not have.

That is not hypothetical. A generated agent shipped with:

    allow = ["read", "write", "edit", "find", "ls", "exec", "web_search", "web_fetch", "browser"]

It then had to babysit a 20GB download, and the only way left was to block a whole turn on
`powershell -Command "Start-Sleep -Seconds 90; ssh ..."` — no progress output, a turn burned
per check, and a 30-minute ceiling. Nothing was broken; the agent was simply reasoning
correctly about a toolbox missing half a pair.

WHY A CURATED MAP AND NOT A SCAN OF DESCRIPTIONS. Tool descriptions name each other constantly
— create_agent's alone mentions `write`, `validate_agent`, `reload_agent`, `create_tool` and
`skill_workshop`. Flagging every mention would fire on agents that are fine, and a check that
cries wolf is a check people switch off. So each pair is listed deliberately, with the reason
it is a pair, and a test asserts every tool named here actually exists in the runtime — the map
cannot quietly rot into naming tools nobody ships.
"""

from __future__ import annotations

from .finding import Finding, WARN

# tool -> (companion it cannot work without, why)
COMPANION_TOOLS: dict[str, tuple[str, str]] = {
    "exec": (
        "process",
        "`exec(background=true)` returns a session id and nothing else — `process` is the only "
        "way to poll it, read its output, or kill it. Without the pair, the model cannot run "
        "anything long without blocking a whole turn on a sleep",
    ),
}


class ToolGrantRules:
    """Does `[tools] allow` grant a tool without the companion it depends on?"""

    name = "grants"

    def check(self, spec, raw_toml: dict, files: list[str]) -> list[Finding]:
        tools = (raw_toml or {}).get("tools")
        if not isinstance(tools, dict):
            return []
        allow = tools.get("allow")
        # No allow list at all means "everything this agent's tier offers" — nothing is being
        # withheld, so there is no half-pair to report. Only an explicit list can be wrong.
        if not isinstance(allow, list):
            return []

        granted = {str(t).strip() for t in allow if str(t).strip()}
        deny = tools.get("deny")
        denied = (
            {str(t).strip() for t in deny if str(t).strip()} if isinstance(deny, list) else set()
        )

        out: list[Finding] = []
        for tool, (companion, why) in sorted(COMPANION_TOOLS.items()):
            if tool not in granted or companion in granted:
                continue
            # Deny is explicit intent: the author said no. Say it differently rather than
            # telling them to add something they deliberately removed.
            if companion in denied:
                out.append(
                    Finding(
                        level=WARN,
                        code="COMPANION_TOOL_DENIED",
                        message=f"allows `{tool}` but DENIES `{companion}` — {why}",
                        path="agent.toml",
                        fix=f"if that is deliberate, tell the agent in its AGENTS.md that "
                        f"`{tool}` background sessions are unavailable, so it does not reach "
                        f"for one and get stuck",
                    )
                )
                continue
            out.append(
                Finding(
                    level=WARN,
                    code="COMPANION_TOOL_MISSING",
                    message=f"allows `{tool}` but not `{companion}` — {why}",
                    path="agent.toml",
                    fix=f'add "{companion}" to [tools] allow, then reload_agent so the change '
                    f"takes effect",
                )
            )
        return out

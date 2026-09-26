"""ExecGrant — does this agent's shell exist?

One answer shared by every rule that depends on it: declarations (every declared setting is in
each `exec` command's environment, so an agent that can run commands READS every setting it
declares) and portability (an agent that runs commands must say what those commands leave behind
in `.agentdignore`).

Granted when [tools] allow names `exec`, or when there is no allow list at all (the full
catalog), unless [tools] deny takes it away. Pure: parsed agent.toml in, answer out.
"""

from __future__ import annotations


class ExecGrant:
    @staticmethod
    def granted(raw: dict) -> bool:
        tools = raw.get("tools") if isinstance(raw.get("tools"), dict) else {}
        allow, deny = tools.get("allow"), tools.get("deny") or ()
        if "exec" in {str(t).strip() for t in deny}:
            return False
        return allow is None or "exec" in {str(t).strip() for t in allow}

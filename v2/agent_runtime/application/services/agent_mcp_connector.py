"""AgentMcpConnector — bring one agent's declared MCP servers up, once, when it first runs.

WHY THIS IS NOT `mcp.add`. That path connects a server and writes it to the machine's
``agentd.config.json``: one flat list, one namespace, not packaged. It is the right tool for "I,
the operator, want this server on this machine" and the wrong one for "this agent needs this
server wherever it is installed". Two agents cannot both have an ``aws`` there, and neither
survives being published. Declared servers therefore live only in the AgentSpec and in this
object's cache, and their tools go into a per-agent set.

FOUR RULES, and each exists because of a specific way this goes wrong:

  LAZY.        Connect on the agent's first run, not at boot or install. Settings are filled in
               after installing, so connecting earlier would mostly connect nothing; and an agent
               nobody opens should not be launching subprocesses.

  REFUSE ON A MISSING CREDENTIAL. A declaration referencing ``${AWS_ACCESS_KEY_ID}`` that the
               user has not filled in must NOT connect. The child inherits the daemon's
               environment, so it would launch and quietly act on whatever account the daemon
               itself holds — the failure that looks like success.

  LAUNCH WITHOUT ASKING. ``command = ["uvx", …]`` downloads and runs third-party code, and
               this used to require the user to approve the exact command once. It was removed:
               the person who asked for the agent had already asked for the integration, and the
               prompt landed on them with no way to see WHY a server was down — a declared agent
               simply had no tools until they found a button in a settings page. A DECLARED server
               is now brought up on first run, full stop. The credential refusal below is what
               still stops a server acting on the wrong account.

  ONE ATTEMPT PER STATE. A failure is remembered with its reason and not retried every turn: a
               server that is down would otherwise add its timeout to every message the user
               sends. Changing a setting clears the memory.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")


class AgentMcpConnector:
    """:param connect: async ``(agent_id, decl, resolved_env, resolved_headers) -> list[Tool]``.
        Injected, so this service never imports the MCP SDK — or knows one exists.
    :param read_env: ``(name) -> str`` for one RESOLVED env var name (already prefixed for the
        agent by the caller's rule). Injected for the same reason: reading the process
        environment is not the application layer's job.
    """

    def __init__(self, connect, read_env, setting_env, oauth=None):
        self._connect = connect
        self._read_env = read_env
        self._setting_env = setting_env
        self._oauth = oauth
        #: agent_id -> {server name: list[Tool]} for servers that are UP
        self._tools: dict[str, dict[str, list]] = {}
        #: agent_id -> {server name: why it is not up} — shown to the model, not swallowed
        self._failed: dict[str, dict[str, str]] = {}

    # ---- what the run needs -------------------------------------------------

    def tools_for(self, agent_id: str) -> list:
        """Every tool this agent's declared servers currently provide (empty until connected)."""
        return [t for tools in self._tools.get(agent_id, {}).values() for t in tools]

    def problems_for(self, agent_id: str) -> dict:
        """``{server: reason}`` for declared servers that are NOT up.

        Surfaced rather than logged-and-forgotten: "this agent has no tools" and "this agent's
        AWS key is missing" look identical to whoever is using it, and only one of them is
        something they can fix.
        """
        return dict(self._failed.get(agent_id, {}))

    async def ensure(self, agent) -> None:
        """Connect whatever this agent declares and has not already settled. Never raises.

        Never raising is not defensiveness: this runs at the top of a turn, and an unreachable
        third-party server must not stop the agent from answering. The reason lands in
        ``problems_for`` and travels to the model instead.
        """
        declarations = tuple(getattr(agent, "mcp", ()) or ())
        if not declarations:
            return
        agent_id = agent.id
        up = self._tools.setdefault(agent_id, {})
        failed = self._failed.setdefault(agent_id, {})
        for decl in declarations:
            if decl.name in up or decl.name in failed:
                continue  # settled — success is cached, failure is not retried every turn
            try:
                await self._bring_up(agent_id, decl, up, failed)
            except Exception as e:  # noqa: BLE001 — one bad server never breaks the turn
                failed[decl.name] = f"{type(e).__name__}: {e}"
                log.warning("agent %s: MCP '%s' failed: %s", agent_id, decl.name, e)

    async def _bring_up(self, agent_id: str, decl, up: dict, failed: dict) -> None:
        missing = self._missing_settings(agent_id, decl)
        if missing:
            failed[decl.name] = (
                f"needs {', '.join(missing)} — set it in this agent's settings, then try again"
            )
            return
        headers = self._resolve(agent_id, decl.headers)
        if decl.auth.startswith("oauth:"):
            token = await self._oauth_token(agent_id, decl.auth.split(":", 1)[1])
            if not token:
                failed[decl.name] = (
                    f"not signed in — connect '{decl.auth.split(':', 1)[1]}' in this agent's "
                    f"settings, then try again"
                )
                return
            headers["Authorization"] = f"Bearer {token}"
        tools = await self._connect(agent_id, decl, self._resolve(agent_id, decl.env), headers)
        if not tools:
            failed[decl.name] = f"connected to '{decl.name}' but it advertises no tools"
            return
        up[decl.name] = list(tools)
        log.info("agent %s: MCP '%s' -> %d tool(s)", agent_id, decl.name, len(tools))

    # ---- credentials --------------------------------------------------------

    async def _oauth_token(self, agent_id: str, name: str) -> str:
        """A live token for a server that authenticates with a sign-in rather than a pasted key.

        Fetched at CONNECT time and sent as a bearer header. A long-lived session outliving the
        token is a real limitation and an honest one: the fix is that a 401 drops the server, the
        next run reconnects, and the refresh happens then — not a token silently going stale
        inside a session nobody re-examines.
        """
        if self._oauth is None:
            return ""
        return await self._oauth(agent_id, name)

    def _missing_settings(self, agent_id: str, decl) -> list[str]:
        """Which of this declaration's ``${NAME}``s have no value for THIS agent.

        Checked against the agent's own (prefixed) storage, so "the daemon exports one" is not an
        answer. That is the whole point: an unfilled AWS key must read as missing, not as the
        daemon's.
        """
        return [
            name
            for name in decl.placeholders
            if not self._read_env(self._setting_env(agent_id, name))
        ]

    def _resolve(self, agent_id: str, values: dict | None) -> dict:
        """``{k: "${NAME}"}`` -> ``{k: <value>}``, resolved for this agent.

        Resolved HERE and handed down already-literal, because the infrastructure that launches
        the child expands ``${…}`` from ``os.environ`` and has no idea which agent is asking —
        it would find the machine-wide variable or nothing at all.
        """
        from agent_runtime.domain.agent import PLACEHOLDER_NAMES

        out = {}
        for key, raw in (values or {}).items():
            out[key] = PLACEHOLDER_NAMES.sub(
                lambda m: self._read_env(self._setting_env(agent_id, m.group(1))), str(raw)
            )
        return out

    # ---- consent ------------------------------------------------------------

    # ---- invalidation -------------------------------------------------------

    def forget(self, agent_id: str) -> None:
        """Drop this agent's cached state so the next run re-dials.

        Called when a setting it uses changes (the running child holds the env it launched with,
        so a new key does nothing until the process is replaced).
        """
        self._tools.pop(agent_id, None)
        self._failed.pop(agent_id, None)

    def agents_using(self, agents, names: set[str]) -> list[str]:
        """Which of ``agents`` declare an MCP server referencing any of ``names``."""
        return [
            a.id
            for a in agents
            if any(set(d.placeholders) & names for d in (getattr(a, "mcp", ()) or ()))
        ]

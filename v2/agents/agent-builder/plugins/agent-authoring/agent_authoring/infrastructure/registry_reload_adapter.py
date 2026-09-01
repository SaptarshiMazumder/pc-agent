"""RegistryReloadAdapter — make a freshly-authored agent REAL, in three steps.

Writing files changes nothing the daemon has already read. Exactly three things are cached, and
this adapter refreshes all of them, then tells the clients:

  1. the agent DEFINITION   -> FileAgentRegistry.refresh() / add()   (re-parses agent.toml)
  2. its PRIVATE PLUGINS    -> register_plugin_live()                (re-scans agents/*/plugins/,
                               replaces the private-tool map wholesale, so it is idempotent)
  3. its DECLARED MCP       -> AgentMcpConnector.forget()            (drops the cached outcome so
                               the next run re-dials — see below)
  4. every connected client -> broadcast_agents_changed()            (the sidebar redraws)

STEP 3 IS NOT OPTIONAL, and leaving it out cost a real build an hour. The connector settles each
declared server ONCE and caches the outcome — success is kept, and failure is deliberately not
retried every turn so a dead server does not add its timeout to every message. Reloading re-reads
agent.toml, so the [[mcp]] block the author just FIXED is live while the connector still holds the
verdict it reached about the old one. The author then edits, reloads, re-runs, sees the identical
failure, and concludes the edit did nothing.

NOT here, on purpose — these are already live and need no nudge:
  * SKILLS — container.resolve_skills() re-reads the skills dirs on EVERY turn.
  * ui/    — the gateway serves it off disk per request with Cache-Control: no-store.

Why this wraps the CONCRETE FileAgentRegistry rather than the AgentRegistry port: the port
declares only resolve/get/list_ids. `add()` and `refresh()` live on the concrete class. Confining
that dependency to this one adapter is precisely what the outermost ring is for.
"""

from __future__ import annotations


class RegistryReloadAdapter:
    def __init__(
        self,
        registry,
        register_plugin_live=None,
        broadcast_agents_changed=None,
        mcp_connector=None,
    ):
        self._registry = registry
        self._reload_plugins = register_plugin_live
        self._broadcast = broadcast_agents_changed
        self._mcp = mcp_connector

    def reload(self, agent_id: str) -> dict:
        """Refresh definition + private tools, announce, and report what actually happened.

        Every step is independent and best-effort: a failure in one is reported rather than
        raised, because a partial reload is still useful (and the tool must never crash the turn).
        """
        result: dict = {
            "agentId": agent_id,
            "definition": False,  # did the registry re-read succeed?
            "found": False,  # is this agent actually in the roster afterwards?
            "tools": None,
            "mcp": None,  # how many [[mcp]] servers it declares (None = connector absent)
            "announced": False,
        }

        # 1. definition — refresh() re-reads EVERY agent, which also picks up a brand-new dir.
        try:
            ids = self._registry.refresh()
            result["definition"] = True
            result["known"] = list(ids) if ids is not None else self._registry.list_ids()
        except Exception as e:  # noqa: BLE001 — surfaced to the model, not raised
            result["error"] = f"registry refresh failed: {type(e).__name__}: {e}"
            return result

        if agent_id not in (result.get("known") or []):
            # The refresh worked; this agent simply does not exist. Distinct from a failure —
            # the caller must not report "reloaded" for an agent that was never there.
            result["error"] = f"'{agent_id}' is still not in the roster after a refresh"
            return result
        result["found"] = True

        # 2. private plugins — a wholesale re-scan; new agents/<id>/plugins/ join the catalog.
        if callable(self._reload_plugins):
            try:
                reloaded = self._reload_plugins() or {}
                if reloaded.get("ok"):
                    result["tools"] = (reloaded.get("agentTools") or {}).get(agent_id, 0)
                else:
                    result["error"] = f"plugin reload failed: {reloaded.get('error')}"
            except Exception as e:  # noqa: BLE001
                result["error"] = f"plugin reload failed: {type(e).__name__}: {e}"

        # 3. declared MCP — the definition just changed, so any verdict cached about the OLD
        # declarations is now about a file that no longer exists.
        if self._mcp is not None:
            try:
                self._mcp.forget(agent_id)
                spec = self._registry.get(agent_id)
                result["mcp"] = len(tuple(getattr(spec, "mcp", ()) or ()))
            except Exception as e:  # noqa: BLE001
                result["error"] = f"mcp reset failed: {type(e).__name__}: {e}"

        # 4. announce — without this the agent works but no sidebar knows it exists.
        if callable(self._broadcast):
            try:
                self._broadcast()
                result["announced"] = True
            except Exception as e:  # noqa: BLE001
                result["error"] = f"broadcast failed: {type(e).__name__}: {e}"
        return result

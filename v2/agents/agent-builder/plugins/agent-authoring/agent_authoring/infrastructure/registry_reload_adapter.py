"""RegistryReloadAdapter — make a freshly-authored agent REAL, in three steps.

Writing files changes nothing the daemon has already read. Exactly two things are cached, and
this adapter refreshes both, then tells the clients:

  1. the agent DEFINITION   -> FileAgentRegistry.refresh() / add()   (re-parses agent.toml)
  2. its PRIVATE PLUGINS    -> register_plugin_live()                (re-scans agents/*/plugins/,
                               replaces the private-tool map wholesale, so it is idempotent)
  3. every connected client -> broadcast_agents_changed()            (the sidebar redraws)

NOT here, on purpose — these are already live and need no nudge:
  * SKILLS — container.resolve_skills() re-reads the skills dirs on EVERY turn.
  * ui/    — the gateway serves it off disk per request with Cache-Control: no-store.

Why this wraps the CONCRETE FileAgentRegistry rather than the AgentRegistry port: the port
declares only resolve/get/list_ids. `add()` and `refresh()` live on the concrete class. Confining
that dependency to this one adapter is precisely what the outermost ring is for.
"""

from __future__ import annotations


class RegistryReloadAdapter:
    def __init__(self, registry, register_plugin_live=None, broadcast_agents_changed=None):
        self._registry = registry
        self._reload_plugins = register_plugin_live
        self._broadcast = broadcast_agents_changed

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

        # 3. announce — without this the agent works but no sidebar knows it exists.
        if callable(self._broadcast):
            try:
                self._broadcast()
                result["announced"] = True
            except Exception as e:  # noqa: BLE001
                result["error"] = f"broadcast failed: {type(e).__name__}: {e}"
        return result

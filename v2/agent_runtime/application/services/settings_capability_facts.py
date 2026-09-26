"""SettingsCapabilityFacts — which of the agent's declared settings the caller has filled.

Presence only, never values. It is what lets the manager tell "blocked: no credentials" (true —
ask the user) from "blocked: no credentials" said by a developer who never checked.
"""

from __future__ import annotations

from agent_runtime.application.run_context import current_setting_value
from agent_runtime.domain.capability_sheet import CapabilityFact, KnownLimitation


class SettingsCapabilityFacts:
    def __init__(self, agent) -> None:
        self._agent = agent

    def facts(self) -> list[CapabilityFact]:
        fields = list(getattr(self._agent, "settings", ()) or ())
        if not fields:
            return []
        state = ", ".join(
            f"{f.key} ({'filled' if current_setting_value(f.key) else 'NOT filled'})" for f in fields
        )
        return [
            CapabilityFact(
                "settings",
                f"declared settings: {state}. Filled ones are in every command's environment "
                "under their own names; a missing one can only be filled by the user in the "
                "agent's Settings page.",
            )
        ]

    def limitations(self) -> list[KnownLimitation]:
        return []


__all__ = ["SettingsCapabilityFacts"]

"""CapabilityFactsSource — anything in the runtime that can describe what it lets an agent do.

Each part states its own facts next to its own code (the command sandbox knows it runs Linux
microVMs with Python; the settings layer knows which declared settings are filled), so the
manager's picture of the organisation is read from the system rather than written about it.
"""

from __future__ import annotations

from typing import Protocol

from agent_runtime.domain.capability_sheet import CapabilityFact, KnownLimitation


class CapabilityFactsSource(Protocol):
    def facts(self) -> list[CapabilityFact]: ...

    def limitations(self) -> list[KnownLimitation]: ...


__all__ = ["CapabilityFactsSource"]

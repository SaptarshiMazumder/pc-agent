"""CapabilitySheetAssembler — builds the manager's picture of the organisation for one run.

The run's actual tools (the same list the developer is given), plus whatever each facts source
states about itself. Read at call time, so a new tool or a sandbox change shows up without anyone
editing a document.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.capability_facts_source import CapabilityFactsSource
from agent_runtime.domain.capability_sheet import CapabilitySheet

_DESC_CHARS = 160


class CapabilitySheetAssembler:
    def __init__(self, sources: list[CapabilityFactsSource]) -> None:
        self._sources = sources

    def assemble(self, tools: list) -> CapabilitySheet:
        return CapabilitySheet(
            tools=tuple((t.name, _first_sentence(getattr(t, "description", ""))) for t in tools),
            facts=tuple(f for s in self._sources for f in s.facts()),
            limitations=tuple(lim for s in self._sources for lim in s.limitations()),
        )


def _first_sentence(text: str) -> str:
    flat = " ".join(str(text or "").split())
    cut = flat.split(". ", 1)[0]
    return cut if len(cut) <= _DESC_CHARS else cut[:_DESC_CHARS] + "…"


__all__ = ["CapabilitySheetAssembler"]

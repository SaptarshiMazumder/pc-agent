"""CapabilitySheet — what the organisation can do, as the manager sees it.

A senior lead does not need the developer's scrollback to know a blocker is fake: they know what
the tools and the environment can do. This is that knowledge, assembled at call time from the
running system — the agent's actual tools, facts each part of the runtime states about itself,
and the known limits recorded in the code that has them. Nothing here is hand-written prose that
can drift from the code.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CapabilityFact:
    topic: str  # e.g. "commands"
    text: str


@dataclass(frozen=True)
class KnownLimitation:
    text: str  # a real platform limit, so the manager blames the platform, not the developer


@dataclass(frozen=True)
class CapabilitySheet:
    tools: tuple[tuple[str, str], ...] = ()  # (name, one-line description)
    facts: tuple[CapabilityFact, ...] = ()
    limitations: tuple[KnownLimitation, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def render(self) -> str:
        out = ["TOOLS THE DEVELOPER HAS:"]
        out += [f"  - {name}: {desc}" for name, desc in self.tools]
        if self.facts:
            out.append("ENVIRONMENT:")
            out += [f"  - ({f.topic}) {f.text}" for f in self.facts]
        if self.limitations:
            out.append("KNOWN PLATFORM LIMITS (the platform's fault, not the developer's):")
            out += [f"  - {lim.text}" for lim in self.limitations]
        out += list(self.notes)
        return "\n".join(out)


__all__ = ["CapabilityFact", "CapabilitySheet", "KnownLimitation"]

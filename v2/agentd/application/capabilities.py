"""CapabilityDescriptor — the ONE read-model for everything the runtime exposes: tools, plugins,
skills, and agents.

Every kind resolves into the SAME shape, so every consumer — the `capabilities.list` RPC, the
model-facing agent roster, the desktop, the terminal — reads capabilities the same way, and the
core never has to know which client is asking. These are pure transforms over data the loaders
already produce (the agent registry, the plugin/tool catalog, the skill loader); the IO lives in
those loaders, and each already ends its description in the uniform `first_meaningful_line` fallback.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class CapabilityDescriptor:
    kind: str                                   # "tool" | "plugin" | "skill" | "agent"
    id: str
    name: str
    description: str
    source: str = ""                            # where it lives (path/plugin id) — lets a client open it
    extra: dict = field(default_factory=dict)   # kind-specific, non-essential metadata

    def as_dict(self) -> dict:
        return asdict(self)


def agent_descriptors(specs) -> list[CapabilityDescriptor]:
    """AgentSpecs -> descriptors. `description` is already the uniformly-resolved one-liner."""
    return [
        CapabilityDescriptor(
            kind="agent", id=s.id, name=(getattr(s, "name", "") or s.id),
            description=(getattr(s, "description", "") or ""),
            source=str(getattr(s, "dir", "") or ""),
            extra={"tagline": getattr(s, "tagline", "") or "",
                   "color": getattr(s, "color", "") or "",
                   "model": getattr(s, "model", None) or ""})
        for s in specs
    ]


def plugin_descriptors(catalog: dict) -> list[CapabilityDescriptor]:
    """build_catalog() output -> one descriptor per plugin (description self-sourced already)."""
    return [
        CapabilityDescriptor(
            kind="plugin", id=pid, name=(p.get("name") or pid),
            description=(p.get("description") or ""),
            extra={"tools": len(p.get("tools") or [])})
        for pid, p in sorted(catalog.items())
    ]


def tool_descriptors(catalog: dict) -> list[CapabilityDescriptor]:
    """build_catalog() output -> one descriptor per tool. `source` is the owning plugin id."""
    out: list[CapabilityDescriptor] = []
    for pid, p in sorted(catalog.items()):
        for t in p.get("tools") or []:
            out.append(CapabilityDescriptor(
                kind="tool", id=t.get("name", "?"), name=t.get("name", "?"),
                description=(t.get("full_description") or t.get("description") or ""),
                source=pid,
                extra={"needs_model": bool(t.get("needs_model")), "model": t.get("model")}))
    return out


def skill_descriptors(skills) -> list[CapabilityDescriptor]:
    """Skill objects -> descriptors. `source` is the SKILL.md path (a client can open it)."""
    return [
        CapabilityDescriptor(
            kind="skill", id=s.name, name=s.name,
            description=(getattr(s, "description", "") or ""),
            source=str(getattr(s, "path", "") or ""),
            extra={"origin": getattr(s, "source", "") or ""})
        for s in skills
    ]

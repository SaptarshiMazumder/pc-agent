"""E2ePathResolver — turn the scenario or trace path a model passes into a real file.

WHY THE CALLER'S AGENT ROOTS. A relative path like `agents/<id>/e2e/x.json` names an agent, and
an agent lives in one of the roots THIS caller can see: the shared catalogue, and on a hosted
daemon their own account's overlay (`<state>/accounts/<acct>/agents`). Resolving against the
static machine root alone meant every agent built on the web was "scenario not found" — the
builder wrote the file, then could not run it without guessing the absolute account path.

The registry answers "which roots" (`agent_roots`), the same answer write scope uses, so a path
the builder could write to is a path it can run.
"""

from __future__ import annotations

from pathlib import Path


class E2ePathResolver:
    def __init__(self, registry) -> None:
        self._registry = registry

    def resolve(self, raw: str, what: str) -> Path | str:
        """The file, or an error STRING naming everything tried — so the fix is obvious."""
        if not raw:
            return f"needs `{what}`"
        p = Path(raw)
        if p.is_absolute():
            return p if p.is_file() else f"{what} not found: {p}"
        # `agents/<id>/...` names the agent from ABOVE a root; `<id>/...` from inside one.
        inner = Path(*p.parts[1:]) if p.parts and p.parts[0] == "agents" and len(p.parts) > 1 else p
        tried: list[str] = []
        for root in self._registry.agent_roots():
            for cand in (Path(root) / inner, Path(root) / p):
                if cand.is_file():
                    return cand
                tried.append(str(cand))
        if p.is_file():
            return p
        tried.append(str(p.resolve()))
        return f"{what} not found — tried: " + "; ".join(dict.fromkeys(tried))


__all__ = ["E2ePathResolver"]

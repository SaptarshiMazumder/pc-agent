"""WorkHandoff — where a long piece of work stands, written so the developer can carry on from it
when its full history no longer fits.

A long build fills the model's context with research, file dumps and tool output until the run
ends on `length` — the work stopped mid-flight, with nothing lost on disk but everything lost from
the developer's head. Once that pressure builds, the model is sent THIS instead of the old history,
followed by the most recent stretch of the work verbatim. The transcript itself is never touched.

Written from the manager's records, not the developer's memory: the user's own words (every one —
they are the requirements), the agreed contract, the plan, the files written, what ran most
recently, and what the manager decided along the way.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.domain.deliverable_contract import DeliverableContract
from agent_runtime.domain.manager_verdict import MANAGER_PREFIX
from agent_runtime.domain.run_digest import DigestEntry

_MAX_USER_CHARS = 2000
_MAX_WORK_LINE = 400


@dataclass(frozen=True)
class WorkHandoff:
    user_messages: tuple[str, ...]
    contract: DeliverableContract | None
    plan: tuple[str, ...]
    files_written: tuple[str, ...]
    recent_work: tuple[DigestEntry, ...]
    decisions: tuple[dict, ...]

    def render(self) -> str:
        out = [
            f"{MANAGER_PREFIX} HANDOFF — the earlier part of this work no longer fits in your "
            "context, so it has been condensed here. The files on disk are unchanged: re-read one "
            "if you need its exact contents. Carry on from where the work stands; do not restart it.",
            "",
            "WHAT THE USER ASKED (verbatim, oldest first):",
        ]
        out += [f"  > {m[:_MAX_USER_CHARS]}" for m in self.user_messages] or ["  (nothing)"]
        out += ["", "CONTRACT:", self.contract.render() if self.contract else "  (none)"]
        out += ["", "YOUR PLAN:"] + ([f"  {p}" for p in self.plan] or ["  (none)"])
        out += ["", "FILES WRITTEN SO FAR:"] + ([f"  {f}" for f in self.files_written] or ["  (none)"])
        out += ["", "WHAT RAN MOST RECENTLY BEFORE THIS POINT:"]
        # One short line per call: a handoff recaps the work, it does not re-carry proof outputs.
        out += [f"  {e.render()[:_MAX_WORK_LINE]}" for e in self.recent_work] or ["  (nothing)"]
        if self.decisions:
            out += ["", "MANAGER DECISIONS SO FAR:"]
            out += [f"  - {d.get('kind')} {d.get('criterion_id') or ''}: {d.get('directive') or d.get('reason') or ''}"
                    for d in self.decisions]
        return "\n".join(out)


__all__ = ["WorkHandoff"]

"""RunDigest — what actually ran since the manager last looked, in facts.

The manager never reads the developer's transcript: its reasoning, the pages it fetched, the files
it dumped. It reads this — each tool call, whether it failed, and a short excerpt of what came
back — next to the developer's own CLAIM about the situation. The two disagreeing ("says Terraform
cannot run here" / never tried to install it) is exactly what a manager is for.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DigestEntry:
    tool: str
    args_excerpt: str
    is_error: bool
    result_excerpt: str

    def render(self) -> str:
        mark = "ERROR" if self.is_error else "ok"
        return f"- {self.tool}({self.args_excerpt}) -> {mark}: {self.result_excerpt}"


@dataclass(frozen=True)
class RunDigest:
    entries: tuple[DigestEntry, ...] = ()

    def render(self) -> str:
        if not self.entries:
            return "(no tool calls since the last checkpoint)"
        return "\n".join(e.render() for e in self.entries)


__all__ = ["DigestEntry", "RunDigest"]

"""NoProgressDetector (#4) — halt when several tool-using turns pass with NO new
information (the same results, or the same errors, again). Catches "busy but going nowhere".

Pure: tracks distinct result digests it has seen — ERRORS INCLUDED. A validator that
reports a different problem each time is telling the model something new, and a repair
loop that converges (emit, fail, fix, fail differently, fix) is progress, not grinding;
only an identical result or an identical error is idle. A turn with no tool calls (pure
thinking / the final answer) is NOT counted as idle — only turns that ran tools but
surfaced nothing new.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.run_observer import ToolEvent


class NoProgressDetector:
    def __init__(self, max_idle_turns: int = 4):
        self.max_idle_turns = max_idle_turns
        self._seen: set[str] = set()
        self._idle = 0
        self._turn_had_tools = False
        self._turn_progress = False

    def reset(self) -> None:
        self._seen.clear()
        self._idle = 0
        self._turn_had_tools = False
        self._turn_progress = False

    def on_tool(self, ev: ToolEvent) -> str | None:
        if ev.phase == "before":
            self._turn_had_tools = True
        elif ev.phase == "after":
            if ev.result_digest and ev.result_digest not in self._seen:
                self._seen.add(ev.result_digest)
                self._turn_progress = True
        return None

    def on_turn(self, index: int) -> str | None:
        fired: str | None = None
        if self._turn_had_tools:
            if self._turn_progress:
                self._idle = 0
            else:
                self._idle += 1
                if self._idle >= self.max_idle_turns:
                    self._idle = 0  # nudge once, then reset
                    fired = (
                        f"The last {self.max_idle_turns} tool-using turns produced no new "
                        f"information (the same results or the same errors again). STOP: change approach, "
                        f"or give the user your best answer from what you DID find plus a clear "
                        f"statement of what you couldn't get. Do not keep grinding."
                    )
        self._turn_had_tools = False
        self._turn_progress = False
        return fired

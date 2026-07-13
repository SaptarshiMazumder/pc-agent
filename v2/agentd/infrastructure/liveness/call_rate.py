"""CallRateBrake (#3) — halt when ONE tool is called too many times in a short
window, REGARDLESS of args. Catches varying-argument flailing (e.g. ten different
web_search queries, or curl scrape after curl scrape) that the per-tool
loop-detection in GuardedTool can't see (it only catches IDENTICAL repeats).

Pure: sees only ToolEvents. After it fires it clears its window, so it nudges
once, then gives the model a fresh window to course-correct before firing again.
"""

from __future__ import annotations

from collections import deque

from agentd.application.interfaces.run_observer import ToolEvent


class CallRateBrake:
    def __init__(self, window: int = 12, max_per_tool: int = 6):
        self.window = window
        self.max_per_tool = max_per_tool
        self._recent: deque[str] = deque(maxlen=window)

    def reset(self) -> None:
        self._recent.clear()

    def on_tool(self, ev: ToolEvent) -> str | None:
        if ev.phase != "before":
            return None
        self._recent.append(ev.name)
        n = sum(1 for x in self._recent if x == ev.name)
        if n > self.max_per_tool:
            self._recent.clear()  # nudge once, then reset the window
            return (
                f"You've called the '{ev.name}' tool {n} times in the last {self.window} "
                f"tool calls — you appear to be repeating the same kind of action without "
                f"progress. STOP this approach: switch to a different tool/strategy, or report "
                f"the blocker (and your best partial answer) to the user. Do not just retry."
            )
        return None

    def on_turn(self, index: int) -> str | None:
        return None

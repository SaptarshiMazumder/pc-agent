"""CallRateBrake (#3) — halt when ONE tool is called many times in a short window and the
earlier calls brought NOTHING NEW. Catches varying-argument flailing (curl scrape after curl
scrape that all fail the same way) which the per-tool loop-detection in GuardedTool can't see
(it only catches IDENTICAL repeats).

WHY "NOTHING NEW" AND NOT JUST "MANY". A sweep of distinct lookups — seven `comfy_node_spec`
calls for seven node classes, each returning a spec never seen before — is research, and
interrupting it mid-flight ("STOP, produce something") made a model ship a graph built around
whatever it had already seen instead of the model it was about to read up on. So the brake
counts calls, but fires only when at most one of the earlier same-tool calls in the window
returned a result this run had not seen. Pure: sees only ToolEvents. After it fires it clears
its window, so it nudges once, then gives the model a fresh window to course-correct.
"""

from __future__ import annotations

from collections import deque

from agent_runtime.application.interfaces.run_observer import ToolEvent


class CallRateBrake:
    def __init__(self, window: int = 12, max_per_tool: int = 6):
        self.window = window
        self.max_per_tool = max_per_tool
        #: [tool name, fresh] per call in the window; `fresh` is None until the call returns.
        self._recent: deque[list] = deque(maxlen=window)
        self._seen: set[str] = set()

    def reset(self) -> None:
        self._recent.clear()
        self._seen.clear()

    def on_tool(self, ev: ToolEvent) -> str | None:
        if ev.phase == "after":
            for entry in reversed(self._recent):
                if entry[0] == ev.name and entry[1] is None:
                    fresh = bool(ev.result_digest) and ev.result_digest not in self._seen
                    if ev.result_digest:
                        self._seen.add(ev.result_digest)
                    entry[1] = fresh
                    break
            return None
        if ev.phase != "before":
            return None
        self._recent.append([ev.name, None])
        same = [e for e in self._recent if e[0] == ev.name]
        n = len(same)
        if n <= self.max_per_tool:
            return None
        fresh_before = sum(1 for e in same[:-1] if e[1])
        if fresh_before > 1:
            return None  # the earlier calls were bringing new information: that is a sweep
        self._recent.clear()  # nudge once, then reset the window
        return (
            f"You've called the '{ev.name}' tool {n} times in the last {self.window} "
            f"tool calls and the earlier calls brought nothing new — you appear to be repeating "
            f"the same kind of action without progress. STOP this approach: switch to a different "
            f"tool/strategy, or report the blocker (and your best partial answer) to the user. "
            f"Do not just retry."
        )

    def on_turn(self, index: int) -> str | None:
        return None

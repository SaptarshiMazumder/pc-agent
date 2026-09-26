"""RunDigestRecorder — one per run: records what ran, hands it to the manager, spots drift.

Per RUN, never shared. The engine's liveness observers are engine-wide singletons reset at run
start, so two concurrent runs share their counters; the manager's view of a run must be that
run's alone.

DRIFT is activity without progress, counted since the last file write:
  * many tool calls and no write           — busy, not building
  * many research calls and no write       — the research spiral that ran a build out of context
  * the same call failing again and again  — a loop the developer cannot see from inside
Each signal is reported once and its counter reset, so the manager is not paged every step.
"""

from __future__ import annotations

import json

from agent_runtime.domain.run_digest import DigestEntry, RunDigest

WRITE_TOOLS = frozenset({"write", "edit", "create_tool", "create_agent", "build_app"})
RESEARCH_TOOLS = frozenset({"web_search", "web_fetch", "read", "grep", "find", "ls"})

_ARGS_CHARS = 160
_RESULT_CHARS = 300
#: Tools whose output IS the evidence a manager judges a criterion by — a window's on-screen
#: tree, a scenario's verdict, an agent's reply. Cut at 300 characters, verify_app's report lost
#: exactly the part that showed the filtered article and its read status, the manager kept saying
#: "not shown", and the developer re-ran the check for fifteen minutes. They keep far more.
EVIDENCE_TOOLS = frozenset({"verify_app", "e2e_run", "e2e_replay", "run_agent"})
_EVIDENCE_HEAD = 1600
_EVIDENCE_TAIL = 600
_WORK_LOG_KEPT = 40  # what a handoff shows of the recent past


class RunDigestRecorder:
    def __init__(
        self,
        calls_without_write: int = 20,
        research_without_write: int = 8,
        repeated_failures: int = 3,
    ) -> None:
        self._calls_limit = calls_without_write
        self._research_limit = research_without_write
        self._repeat_limit = repeated_failures
        self._pending: list[DigestEntry] = []
        self._total = 0
        self._since_write = 0
        self._research_since_write = 0
        self._last_failure: tuple[str, str] | None = None
        self._failure_streak = 0
        self._drift: list[str] = []
        self._tool_failures: dict[str, int] = {}  # tool -> consecutive failures, any arguments
        self._work_log: list[DigestEntry] = []  # every call, kept past `take` for handoffs
        self._files_written: dict[str, None] = {}  # insertion-ordered set of written paths

    @property
    def total_calls(self) -> int:
        return self._total

    def record(self, name: str, args: dict, is_error: bool, result_text: str) -> None:
        args_text = _excerpt(json.dumps(args or {}, ensure_ascii=False, default=str), _ARGS_CHARS)
        result = _evidence(result_text) if name in EVIDENCE_TOOLS else _excerpt(result_text, _RESULT_CHARS)
        entry = DigestEntry(name, args_text, bool(is_error), result)
        self._pending.append(entry)
        self._work_log.append(entry)
        del self._work_log[:-_WORK_LOG_KEPT]
        self._total += 1
        if name in WRITE_TOOLS and not is_error:
            self._since_write = 0
            self._research_since_write = 0
            path = str((args or {}).get("path") or "")
            if path:
                self._files_written[path] = None
        else:
            self._since_write += 1
            if name in RESEARCH_TOOLS:
                self._research_since_write += 1
        self._track_failures(name, args_text, is_error)
        self._track_tool_failures(name, is_error)
        self._check_drift()

    def take(self) -> RunDigest:
        """What ran since the last take — the manager sees each call once."""
        digest = RunDigest(tuple(self._pending))
        self._pending = []
        return digest

    def work_log(self) -> tuple[DigestEntry, ...]:
        """The most recent calls, whether or not the manager has already seen them."""
        return tuple(self._work_log)

    def files_written(self) -> tuple[str, ...]:
        return tuple(self._files_written)

    def drift(self) -> list[str]:
        """Drift signals raised since the last call, each reported once."""
        out, self._drift = self._drift, []
        return out

    def _track_failures(self, name: str, args_text: str, is_error: bool) -> None:
        key = (name, args_text)
        if is_error and key == self._last_failure:
            self._failure_streak += 1
        elif is_error:
            self._last_failure, self._failure_streak = key, 1
        else:
            self._last_failure, self._failure_streak = None, 0
        if self._failure_streak >= self._repeat_limit:
            self._drift.append(f"the same call failed {self._failure_streak} times in a row: {name}({args_text})")
            self._failure_streak = 0

    def _track_tool_failures(self, name: str, is_error: bool) -> None:
        """The same TOOL failing again and again, whatever its arguments. Varying the target on
        every try hid a fifteen-minute loop from the exact-call check above."""
        if not is_error:
            self._tool_failures[name] = 0
            return
        self._tool_failures[name] = self._tool_failures.get(name, 0) + 1
        if self._tool_failures[name] >= self._repeat_limit:
            self._drift.append(
                f"{name} has failed {self._tool_failures[name]} times in a row with different "
                "attempts — the approach, not the arguments, is what is not working"
            )
            self._tool_failures[name] = 0

    def _check_drift(self) -> None:
        if self._since_write >= self._calls_limit:
            self._drift.append(f"{self._since_write} tool calls since the last file write")
            self._since_write = 0
        if self._research_since_write >= self._research_limit:
            self._drift.append(
                f"{self._research_since_write} research calls (search/fetch/read) since the last file write"
            )
            self._research_since_write = 0


def _evidence(text: str) -> str:
    """A proof tool's output: its start (what was found, what is on screen) and its end (the
    verdict), with only the middle trimmed."""
    flat = " ".join(str(text or "").split())
    if len(flat) <= _EVIDENCE_HEAD + _EVIDENCE_TAIL:
        return flat
    return flat[:_EVIDENCE_HEAD] + " … " + flat[-_EVIDENCE_TAIL:]


def _excerpt(text: str, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


__all__ = ["RESEARCH_TOOLS", "WRITE_TOOLS", "RunDigestRecorder"]

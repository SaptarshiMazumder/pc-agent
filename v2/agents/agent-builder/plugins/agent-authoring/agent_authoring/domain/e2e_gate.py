"""E2eGate — a batch of building is not finished until it has been PROVEN to work.

WHY THIS IS AN OBSERVER AND NOT A SENTENCE IN A SKILL. The instruction to test already existed,
in prose, and a live session showed exactly what prose is worth: the builder wrote an agent, ran
its tools once by hand, said "News Clipper is built and working -- try it out", and had never
authored a scenario or called e2e_run. It was not disobeying. The procedure it follows ends at
"does it actually DO it", and nothing sent it to the testing page. The same lesson was learned
for the `suggest` block one commit earlier: a prose section is advice, and two different models
ignored it until it became a numbered rule.

So this is the choke point instead. The agent loop asks its observers at every turn boundary
whether the run may proceed; a reason returned here is injected back into the conversation and
the model is sent round again. It cannot finish a batch it has not tested, because finishing is
the thing being gated -- not shipping. `validate_agent` and `package_agent` are gates you walk
into; this one is the doorway itself.

WHAT COUNTS AS PROOF
  e2e_run        the real thing: a scenario driven against the built agent
  verify_app     accepted ONLY when every change since the last proof was window source
                 (app/, ui/) -- a screen is proven by opening it, not by a scenario
  skip_e2e       the user's own waiver, and only theirs. It lasts for THIS RUN: the next
                 batch asks again, because a person who skipped one test did not sign a
                 blanket exemption.

WHICH TURN IS "THE END OF THE BATCH". The loop notifies observers twice: once after a round of
tool calls (the model is mid-build, and nagging there would interrupt work it is doing correctly)
and once when it produced NO tool calls, which is the moment it is about to answer. Only the
second is a batch boundary, and the two are told apart by whether any tool fired since the last
tick -- the signature carries a turn index and nothing else.

PURE. It sees ToolEvents and a turn index, decides, and holds no handles. What it cannot see --
whether the user was asked for a URL, whether a provider was down -- belongs in the message it
returns, which is written to be acted on.
"""

from __future__ import annotations

#: Tools whose success changes what an agent IS. A batch containing one of these owes a test.
MUTATORS = frozenset({"write", "edit", "create_tool", "skill_workshop", "create_agent"})

#: Seeing one of these is how the gate knows this run is BUILDING an agent rather than doing
#: something else with a shell and a text editor. The gate is inert until then, so it never
#: fires on a run that is not the builder's.
AUTHORING = frozenset({
    "create_agent", "create_tool", "skill_workshop", "scaffold_react_app",
    "build_app", "verify_app", "validate_agent", "package_agent", "publish_agent",
})

PROOF = "e2e_run"
WINDOW_PROOF = "verify_app"
WAIVER = "skip_e2e"

#: A change confined to these is window source, and `verify_app` is the honest proof for it.
WINDOW_MARKERS = ("/app/", "/ui/", "app/", "ui/")

#: How many paths to name back to the model. Enough to be actionable, not a wall.
_NAME_LIMIT = 4


def _is_window_path(path: str) -> bool:
    p = (path or "").replace("\\", "/")
    return any(m in p for m in WINDOW_MARKERS)


class E2eGate:
    """Returns a halt reason from `on_turn` while this run has unproven changes."""

    name = "e2e-gate"

    def __init__(self) -> None:
        self.reset()

    # ------------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        self._armed = False
        self._dirty: list[str] = []
        self._window_only = True
        self._waived = False       # per RUN, by construction: reset() clears it
        self._tools_since_turn = False
        self._asked = 0

    # ------------------------------------------------------------------ signals
    def on_tool(self, ev) -> str | None:
        if getattr(ev, "phase", "") != "after":
            return None
        self._tools_since_turn = True
        name = getattr(ev, "name", "") or ""
        if name in AUTHORING:
            self._armed = True
        if getattr(ev, "is_error", False):
            return None                      # a tool that failed proved nothing and dirtied nothing

        if name == WAIVER:
            self._waived = True
            self._clear()
        elif name == PROOF:
            self._clear()
        elif name == WINDOW_PROOF and self._window_only:
            self._clear()
        elif name in MUTATORS:
            path = self._path_of(ev)
            self._dirty.append(path)
            if not _is_window_path(path):
                self._window_only = False
        return None

    def on_turn(self, index: int) -> str | None:
        # Mid-build: tools ran this round, the model is still working. Not a batch boundary.
        if self._tools_since_turn:
            self._tools_since_turn = False
            return None
        if not (self._armed and self._dirty) or self._waived:
            return None
        self._asked += 1
        return self._message()

    # ------------------------------------------------------------------ internals
    def _clear(self) -> None:
        self._dirty = []
        self._window_only = True

    @staticmethod
    def _path_of(ev) -> str:
        args = getattr(ev, "args", None) or {}
        for key in ("path", "file", "agent_id", "agentId", "name"):
            v = args.get(key)
            if v:
                return str(v)
        return ""

    def _message(self) -> str:
        changed = list(dict.fromkeys(p for p in self._dirty if p))[:_NAME_LIMIT]
        what = ", ".join(changed) if changed else "this agent"
        more = "" if len(self._dirty) <= _NAME_LIMIT else " (and more)"
        return (
            "You changed " + what + more + " and have not proved any of it works. "
            "Do not finish this batch untested. Author or reuse a scenario under the agent's "
            "e2e/ folder (e2e_checks lists the check vocabulary) and run e2e_run over it. "
            "IF THE TEST NEEDS SOMETHING ONLY THE USER HAS -- a live URL, an API key, an "
            "account to sign into -- ASK THEM FOR IT plainly and say what you will do with it; "
            "never invent a credential and never quietly skip. "
            "If the change genuinely cannot be tested any other way (a pure window edit), "
            "verify_app is the proof for that. "
            "If the USER themselves says to skip testing, call skip_e2e with their reason."
        )

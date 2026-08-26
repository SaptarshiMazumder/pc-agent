"""AppAutoBuildObserver — every write to an agent's ``app/`` becomes a build, automatically.

WHY A MECHANISM AND NOT AN INSTRUCTION. `app/` is source and `ui/` is what the daemon serves, so
an edit that is never compiled is invisible: the user reloads, sees the old screen, and every file
they can inspect says the work was done. The skill has always said "run `build_app` after every
change" — and an instruction the model has to remember is exactly the thing that gets skipped on
the one edit that mattered. This removes the remembering: the model edits, the build happens, the
open window reloads itself (`app.rebuilt` → `LiveReload`). The whole loop the hot-reload work
exists for, with no step left to forget.

WHY IT IS NOT A FILE WATCHER. The writes already flow through the daemon — the model edits with
the daemon's own `write`/`edit` tools, and the engine already announces every tool execution to
its run observers. Watching the filesystem for changes we ourselves just made would add a
process per agent to spawn, stop and orphan-clean, and its "rebuilt" signal would still have to
travel through the daemon, because the daemon's socket is the only pipe to the open window.

DEBOUNCED, because the model writes in bursts: ten files in one turn must become one build, not
ten. Each new write to the same agent resets a short timer; the build runs when the burst goes
quiet. The build itself runs in the timer's thread — it is a vite subprocess taking seconds, and
an observer that blocked the engine loop for that long would stall the very turn that did the
writing.

FAILURES ARE LOUD, TWICE. A broken compile logs at WARNING with vite's own message, and the
window is NOT told to reload — a failed build leaves `ui/` untouched, so reloading would repaint
the old screen and read as "my change did nothing" when it actually did not compile. The stale
state is then also what `verify_app` reports. What a failure never is, is silent success.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from agent_runtime.application.interfaces.run_observer import ToolEvent

log = logging.getLogger("agentd")

#: The tools whose "after" means a file changed. Anything else — read, ls, exec — is not a write,
#: and building on it would be noise.
WRITE_TOOLS = frozenset({"write", "edit"})

#: Quiet time after the last write before the build fires. Long enough to fold one turn's burst
#: of edits into one build; short enough that the reload still feels attached to the change.
DEBOUNCE_SECONDS = 2.0


class AppAutoBuildObserver:
    """A RunObserver. Registered by the authoring plugin via the container's observer seam.

    :param registry:  ``list_ids()`` + ``resolve_dir(id)`` — which agent, if any, a path is in.
    :param builder:   BuildAppService — THE one builder, shared with `build_app` and
                      `create_agent`, so an automatic build and an asked-for one cannot disagree
                      about the toolchain.
    :param announce:  the gateway's ``broadcast_app_rebuilt``. Called on the loop, not from the
                      build thread — the gateway's sync wrapper no-ops without a running loop,
                      which from a plain thread is always.
    """

    def __init__(self, registry, builder, announce=None, debounce_s: float = DEBOUNCE_SECONDS):
        self._registry = registry
        self._builder = builder
        self._announce = announce
        self._debounce_s = debounce_s
        self._lock = threading.Lock()
        self._timers: dict[str, threading.Timer] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---------------------------------------------------------------- RunObserver
    def reset(self) -> None:  # a new run changes nothing here
        return None

    def on_turn(self, index: int) -> str | None:
        return None

    def on_tool(self, ev: ToolEvent) -> str | None:
        # AFTER, and only a SUCCESSFUL write: a refused edit changed nothing worth compiling.
        if ev.phase != "after" or ev.is_error or ev.name not in WRITE_TOOLS:
            return None
        agent_id = self._agent_of(str((ev.args or {}).get("path") or ""))
        if not agent_id:
            return None
        # The loop this run is on, captured while we are on it. The build thread needs it to
        # announce the rebuild — see __init__.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass  # keep the last captured loop — off-loop callers (tests) must not erase it
        self._schedule(agent_id)
        return None  # never a halt reason — building is a side effect, not a verdict

    # ---------------------------------------------------------------- internals
    def _agent_of(self, path: str) -> str | None:
        """Which agent's ``app/`` this path is inside, or None.

        Resolved against the REGISTRY's directories rather than by pattern-matching the path:
        agents live in more than one root (the shared catalogue, account overlays), and the
        registry is the only thing that knows all of them.
        """
        if not path or "app" not in path:
            return None
        try:
            p = Path(path).resolve()
        except OSError:
            return None
        for agent_id in self._registry.list_ids():
            d = self._registry.resolve_dir(agent_id)
            if d is None:
                continue
            try:
                rel = p.relative_to(Path(d).resolve())
            except ValueError:
                continue
            if rel.parts and rel.parts[0] == "app":
                return agent_id
        return None

    def _schedule(self, agent_id: str) -> None:
        with self._lock:
            old = self._timers.pop(agent_id, None)
            if old is not None:
                old.cancel()
            timer = threading.Timer(self._debounce_s, self._build, args=(agent_id,))
            timer.daemon = True  # never the thing that keeps a daemon from exiting
            self._timers[agent_id] = timer
            timer.start()

    def _build(self, agent_id: str) -> None:
        with self._lock:
            self._timers.pop(agent_id, None)
        try:
            self._builder.build(agent_id)
        except Exception as e:  # noqa: BLE001 — surfaced below, never raised into a Timer thread
            # LOUD, and no reload: ui/ is untouched, so telling the window to refresh would
            # repaint the old screen and read as "my change did nothing".
            log.warning(
                "auto-build of %s's window failed (%s: %s) — its open window is still showing "
                "the LAST successful build. The next `build_app` shows vite's full error.",
                agent_id,
                type(e).__name__,
                e,
            )
            return
        log.info("auto-built %s's window after an app/ edit", agent_id)
        if self._announce is not None and self._loop is not None:
            # On the loop, because the gateway's fire-and-forget wrapper no-ops off it.
            self._loop.call_soon_threadsafe(self._announce, agent_id)

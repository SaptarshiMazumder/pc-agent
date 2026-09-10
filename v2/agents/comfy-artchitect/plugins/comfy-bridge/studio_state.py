"""studio_state — the run telemetry file the agent's WINDOW reads.

The dashboard ("Studio") shows run history, the active run, the instance's models and render
stats. None of that should come from parsing conversation text: the bridge tools already hold
each fact, structured, at the moment it happens — so they record it here, and the window polls
one tool (`comfy_studio_state`) that hands the file back as `details`.

ONE JSON FILE, in the run's workspace (`.studio/state.json`), because the workspace is the one
place both sides already share: per account on a hosted daemon, written by sandboxed tools
(inside their fs grant), readable by the window through `tools.invoke`. Not a database — a
best-effort mirror whose loss costs nothing but an empty dashboard.

EVERY WRITE IS WHOLE-FILE AND BEST-EFFORT. A telemetry write must never fail a run, so every
entry point swallows its own errors; the caps keep the file small enough that whole-file
rewrite stays cheap.
"""

from __future__ import annotations

import json
import struct
import time
from pathlib import Path

from agent_runtime.application.run_context import current_run_context, current_workspace

#: Newest-first caps. The dashboard shows a page of each; history beyond that is scrollback.
_MAX_RUNS = 50
_MAX_RENDERS = 60


def _file() -> Path:
    return Path(current_workspace(".") or ".") / ".studio" / "state.json"


def _load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    try:
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass  # telemetry, not truth — the run itself must not care


def read() -> dict:
    """What the window gets. Public because the state tool is in another module."""
    return _load()


def set_instance(**fields) -> None:
    """Merge probe/inventory facts: version, gpu, vram, models. Merged rather than replaced so
    a probe (version+vram) and an inventory (models) each keep the other's half."""
    try:
        state = _load()
        instance = state.get("instance") or {}
        instance.update({k: v for k, v in fields.items() if v is not None})
        instance["ts"] = time.time()
        state["instance"] = instance
        _save(state)
    except Exception:  # noqa: BLE001
        pass


def run_started(workflow: str, prompt_id: str, checkpoint: str, steps) -> None:
    try:
        state = _load()
        state["active"] = {
            "workflow": workflow,
            "prompt_id": prompt_id,
            "checkpoint": checkpoint,
            "steps": steps,
            "started": time.time(),
            "elapsed": 0.0,
            "status": "running",
        }
        _save(state)
    except Exception:  # noqa: BLE001
        pass


def run_tick(prompt_id: str) -> None:
    """Refresh the active run's elapsed clock — called from comfy_run's poll loop, so the
    window's Active-run panel moves while the model is still waiting."""
    try:
        state = _load()
        active = state.get("active") or {}
        if active.get("prompt_id") != prompt_id:
            return
        active["elapsed"] = round(time.time() - float(active.get("started") or time.time()), 1)
        state["active"] = active
        _save(state)
    except Exception:  # noqa: BLE001
        pass


def run_finished(prompt_id: str, status: str, outputs: int = 0) -> None:
    """Close the active run into the history. `status`: complete | failed | interrupted."""
    try:
        state = _load()
        active = state.get("active") or {}
        if active.get("prompt_id") not in ("", prompt_id):
            # A different run is active — record this one from what we know.
            active = {"workflow": "", "started": time.time(), "checkpoint": "", "steps": None}
        row = {
            "name": active.get("workflow") or prompt_id,
            "checkpoint": active.get("checkpoint") or "",
            "steps": active.get("steps"),
            "duration": round(time.time() - float(active.get("started") or time.time()), 1),
            "status": status,
            "outputs": outputs,
            "ts": time.time(),
        }
        state["runs"] = ([row] + list(state.get("runs") or []))[:_MAX_RUNS]
        if (state.get("active") or {}).get("prompt_id") == prompt_id:
            state["active"] = None
        _save(state)
    except Exception:  # noqa: BLE001
        pass


def render_saved(path: str) -> None:
    """One downloaded output. PNG dimensions come from the IHDR header — 24 bytes, no image
    library; other formats just skip the dimensions."""
    try:
        p = Path(path)
        entry = {"path": str(p), "filename": p.name, "ts": time.time()}
        try:
            entry["bytes"] = p.stat().st_size
            with open(p, "rb") as f:
                head = f.read(24)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                entry["w"], entry["h"] = int(w), int(h)
        except (OSError, struct.error):
            pass
        state = _load()
        renders = [r for r in (state.get("renders") or []) if r.get("path") != entry["path"]]
        state["renders"] = ([entry] + renders)[:_MAX_RENDERS]
        _save(state)
    except Exception:  # noqa: BLE001
        pass


# ─────────────────────────────── has this job designed anything yet? ───────────────────────────
#
# WHY THIS EXISTS. `comfy_inventory` answers "what is installed". On a box the user owned, that
# was a genuine design constraint — you built with what was there. On a PROVISIONED box it is
# not: anything missing can be downloaded, so the only thing a long list of installed models
# does is tempt the agent into picking from it instead of researching what the job actually
# needs. That is inventory-anchoring, and it gets worse the longer a machine lives.
#
# So inventory is gated until a workflow has been emitted. Before the design exists it can only
# mislead; after it exists it is exactly the right tool for "did the download land". The rule was
# already written down and was still being ignored, which is why it is now mechanical.
#
# PER JOB, NOT PER WORKSPACE. Keyed by session, because the workspace is shared across every
# conversation this account has ever had — "somebody once emitted a workflow here" is not the
# question.

_EMIT_FILE = ".studio/emitted.json"


def _session() -> str:
    ctx = current_run_context()
    return str(getattr(ctx, "session_key", "") or "") if ctx else ""


def mark_emitted() -> None:
    """Record that THIS conversation has produced a design. Best-effort, like every write here."""
    try:
        session = _session()
        if not session:
            return
        path = Path(current_workspace(".") or ".") / _EMIT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            seen = json.loads(path.read_text(encoding="utf-8"))
            sessions = list(seen.get("sessions") or [])
        except (OSError, ValueError):
            sessions = []
        if session not in sessions:
            sessions.append(session)
        path.write_text(json.dumps({"sessions": sessions[-200:]}), encoding="utf-8")
    except Exception:  # noqa: BLE001 — telemetry must never fail a run
        pass


def has_emitted() -> bool:
    """Has this conversation emitted a workflow yet?

    FAILS OPEN. If the session cannot be identified, or the file cannot be read, inventory is
    allowed: a gate that blocks the agent because a marker file was unreadable would break real
    work to enforce a stylistic rule.
    """
    try:
        session = _session()
        if not session:
            return True
        path = Path(current_workspace(".") or ".") / _EMIT_FILE
        return session in (json.loads(path.read_text(encoding="utf-8")).get("sessions") or [])
    except (OSError, ValueError):
        return False
    except Exception:  # noqa: BLE001
        return True

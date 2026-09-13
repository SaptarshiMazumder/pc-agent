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


# ─────────────────────────────── the gates the protocol used to state in prose ─────────────────
#
# WHY THESE ARE MECHANICAL. "Emit, validate, THEN install; present the checkpoint, THEN run" was
# written down in three places and a model ignored all three within an hour of deployment: it
# emitted a four-node stub to unlock inventory, installed three node packs against no workflow,
# and called comfy_node_install with a fake pack name to force a restart. Rules a model can skip
# are suggestions. These are checks: each tool asks a file the previous step wrote, and refuses
# — naming the step — when it is not there.
#
# PER CONVERSATION, like `mark_emitted`: the workspace is shared across every chat this account
# has, and "some chat once validated something" is not the question.
#
# FAIL CLOSED where the answer is knowable. An unreadable record means "no record". Only a run
# whose session cannot be identified at all is let through — there is nothing to key on, and
# blocking every call in that case would break the desktop for a rule about conversations.

_VALIDATED_FILE = ".studio/validated.json"
_FIRST_EMITS_FILE = ".studio/first_emits.json"
#: Written by the DAEMON (agent_runtime/infrastructure/checkpoint_marker.py), read here. The
#: daemon sees the transcript — it knows when a turn ended on a checkpoint and when the user
#: answered — and the tools see the file.
_CHECKPOINT_FILE = ".studio/checkpoint.json"
_MAX_SESSIONS = 200


def _read_json(rel: str) -> dict:
    try:
        data = json.loads((Path(current_workspace(".") or ".") / rel).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(rel: str, data: dict) -> None:
    path = Path(current_workspace(".") or ".") / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _prune(sessions: dict) -> dict:
    if len(sessions) <= _MAX_SESSIONS:
        return sessions
    return dict(list(sessions.items())[-_MAX_SESSIONS:])


def mark_first_emit(name: str) -> None:
    """The moment THIS conversation first emitted a workflow of this name. First only: a
    revision keeps the original time, so a checkpoint presented after the first version still
    covers the revision the user's answer asked for."""
    try:
        session = _session()
        if not session or not name:
            return
        data = _read_json(_FIRST_EMITS_FILE)
        sessions = data.get("sessions") or {}
        mine = sessions.get(session) or {}
        if name not in mine:
            mine[name] = time.time()
            sessions[session] = mine
            _write_json(_FIRST_EMITS_FILE, {"sessions": _prune(sessions)})
    except Exception:  # noqa: BLE001 — a record miss must not fail the emit
        pass


def first_emit_at(name: str) -> float:
    session = _session()
    if not session:
        return 0.0
    return float(((_read_json(_FIRST_EMITS_FILE).get("sessions") or {}).get(session) or {}).get(name) or 0.0)


def mark_validated(name: str, missing_files: list, unknown_classes: list) -> None:
    """What comfy_validate found for this workflow — the ONLY thing that authorises an install."""
    try:
        session = _session()
        if not session or not name:
            return
        data = _read_json(_VALIDATED_FILE)
        sessions = data.get("sessions") or {}
        mine = sessions.get(session) or {}
        mine[name] = {
            "at": time.time(),
            "missing_files": [str(x) for x in missing_files][:200],
            "unknown_classes": [str(x) for x in unknown_classes][:200],
        }
        sessions[session] = mine
        _write_json(_VALIDATED_FILE, {"sessions": _prune(sessions)})
    except Exception:  # noqa: BLE001
        pass


def _validations() -> dict:
    session = _session()
    if not session:
        return {}
    return (_read_json(_VALIDATED_FILE).get("sessions") or {}).get(session) or {}


def validated_names() -> set[str]:
    """The workflows comfy_validate has passed in THIS conversation, by role name — the set whose
    install gate is armed. Read by comfy_delete to know that deleting one is not just losing a
    file but orphaning an authorisation."""
    return set(_validations().keys())


def forget_validated(name: str) -> None:
    """Drop one workflow's validation record — called when its file is deleted, so the install
    gate and the disk agree. Left in place, the record would keep authorising installs for a
    workflow that no longer exists, and the next comfy_run would fail on a missing file with a
    gate that still says 'validated'."""
    try:
        session = _session()
        if not session or not name:
            return
        data = _read_json(_VALIDATED_FILE)
        sessions = data.get("sessions") or {}
        mine = sessions.get(session) or {}
        if name not in mine:
            return
        del mine[name]
        sessions[session] = mine
        _write_json(_VALIDATED_FILE, {"sessions": _prune(sessions)})
    except Exception:  # noqa: BLE001
        pass


def install_allowed(filename: str) -> tuple[bool, str]:
    """May `filename` be installed? Only if a validation in this conversation listed it."""
    if not _session():
        return True, ""
    want = (filename or "").strip().lower()
    for name, rec in _validations().items():
        if any(str(f).strip().lower() == want for f in rec.get("missing_files") or []):
            # NAMED BY A VALIDATION — and the ask has been answered. Installing is Phase 3; the
            # ask is 3.5. It does not matter that a download is free: it is the design being
            # built before anyone said yes.
            ok, why = checkpoint_answered(name)
            return (True, name) if ok else (False, f"'{filename}' is on '{name}'s install list, but nothing is installed before the ask is answered. {why}")
    return False, (
        f"no validated workflow in this conversation names '{filename}' as a missing file, so it "
        "will not be installed. The order is comfy_emit → comfy_validate → comfy_install, and "
        "validate's missing-file list is the ONLY shopping list (hard rule 3). Validate the "
        "workflow that needs this file; if validate does not name it, the graph does not need it."
    )


def node_install_allowed() -> tuple[bool, str]:
    """May a node pack be installed? Only after a validation in this conversation reported a
    node class the instance lacks."""
    if not _session():
        return True, ""
    blocked = ""
    for name, rec in _validations().items():
        if rec.get("unknown_classes"):
            ok, why = checkpoint_answered(name)
            if ok:
                return True, ""
            blocked = blocked or f"'{name}' needs a node pack, but nothing is installed before the ask is answered. {why}"
    if blocked:
        return False, blocked
    return False, (
        "no validated workflow in this conversation reports a missing node class, so no pack will "
        "be installed. Emit the graph, comfy_validate it, and install only what validate names. "
        "A class name you could not find is usually a WRONG NAME — comfy_node_search finds the "
        "real one — not a missing pack. And a pack is never installed to force a restart."
    )


def checkpoint_answered(name: str) -> tuple[bool, str]:
    """May `name` run (or be installed for)? Only after the user has ANSWERED the ask.

    The daemon stamps `presented_at` the moment `ask_user` returns — the ask is a tool call, not
    a block of prose — and `answered_at` when the next user message arrives. The ask comes BEFORE
    the emit (AGENTS.md 3.5), so the only question is whether this conversation's latest ask has
    an answer. A new ask resets the answer, so a new job in the same chat waits for its own yes.
    """
    session = _session()
    if not session:
        return True, ""
    rec = ((_read_json(_CHECKPOINT_FILE).get("sessions") or {}).get(session) or {})
    presented = float(rec.get("presented_at") or 0.0)
    answered = float(rec.get("answered_at") or 0.0)
    how = (
        "Call `ask_user` (AGENTS.md 3.5) with the paid services and their exact dollars and "
        "credits from comfy_price, the brief-check questions with your defaults, and the "
        "workflow(s) by role name — then end the turn. Build and run only in the turn AFTER the "
        "user answers."
    )
    if not presented:
        return False, f"'{name}': no ask has been presented in this conversation. {how}"
    if answered < presented:
        return False, (
            f"'{name}': the ask was presented but the user has not answered it. Do nothing more "
            "this turn — the answer arrives as their next message."
        )
    return True, ""


# ─────────────────────────────── what this conversation brought back ───────────────────────────

_DOWNLOADS_FILE = ".studio/downloads.json"


def mark_downloaded(rel: str) -> None:
    """A render comfy_download saved for THIS conversation — the only outputs comfy_upload will
    send back to the instance. Per conversation, like everything else here: another chat's
    renders are as foreign as its references."""
    try:
        session = _session()
        if not session or not rel:
            return
        data = _read_json(_DOWNLOADS_FILE)
        sessions = data.get("sessions") or {}
        mine = list(sessions.get(session) or [])
        if rel not in mine:
            mine.append(rel)
        sessions[session] = mine[-500:]
        _write_json(_DOWNLOADS_FILE, {"sessions": _prune(sessions)})
    except Exception:  # noqa: BLE001
        pass


_UPLOADS_FILE = ".studio/uploads.json"


def mark_uploaded(server_name: str) -> None:
    """A server-side name comfy_upload returned for THIS conversation — the only literal names a
    loader in an emitted graph may carry besides a slot token (comfy_emit checks)."""
    try:
        session = _session()
        if not session or not server_name:
            return
        data = _read_json(_UPLOADS_FILE)
        sessions = data.get("sessions") or {}
        mine = list(sessions.get(session) or [])
        if server_name not in mine:
            mine.append(server_name)
        sessions[session] = mine[-500:]
        _write_json(_UPLOADS_FILE, {"sessions": _prune(sessions)})
    except Exception:  # noqa: BLE001
        pass


def uploaded_in_session() -> set[str]:
    session = _session()
    if not session:
        return set()
    return set((_read_json(_UPLOADS_FILE).get("sessions") or {}).get(session) or [])


def downloaded_in_session() -> set[str]:
    session = _session()
    if not session:
        return set()
    return set((_read_json(_DOWNLOADS_FILE).get("sessions") or {}).get(session) or [])


def forget_downloaded(rel: str) -> None:
    """Drop one render from this conversation's download record when its file is deleted. The
    record is what lets comfy_upload send an output back up as a later workflow's input; a
    record naming a file that is gone is a promise the run tool cannot keep."""
    try:
        session = _session()
        if not session or not rel:
            return
        data = _read_json(_DOWNLOADS_FILE)
        sessions = data.get("sessions") or {}
        mine = [x for x in (sessions.get(session) or []) if x != rel]
        sessions[session] = mine
        _write_json(_DOWNLOADS_FILE, {"sessions": _prune(sessions)})
    except Exception:  # noqa: BLE001
        pass

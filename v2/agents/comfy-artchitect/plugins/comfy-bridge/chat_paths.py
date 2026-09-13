"""Where THIS chat's files live inside the shared workspace.

THE WORKSPACE IS THE ACCOUNT'S, SHARED BY EVERY CONVERSATION. A flat workflows/ or outputs/
therefore held every chat's files at once — a new conversation's panel listed seven workflows from
older jobs beside its own two — and two chats that named a workflow the same overwrote each
other's. references/ solved this first: one folder per chat, named by the session key. This module
applies the same rule to all three kinds, so the map from a chat to its files is a folder on disk
that the window can list without anything crossing the wire.

THE FOLDER NAME IS A CONTRACT WITH THE WINDOW. app/src/agentd/workspace-files.ts computes the same
`<kind>/<chat>` and lists it through workspace.list; the two must agree or the window looks where
the plugin never writes. Chat keys are already path-safe ("chat-<time>-<rand>"); e2e and peer keys
carry colons, which fold to underscores on both sides.

EVERY PATH THIS MODULE HANDS OUT IS WORKSPACE-RELATIVE AND POSIX. That is the only form that means
the same file on both sides of a sandbox: on a microVM the plugin's workspace is /tmp/exec-<id>/ws
and the host's is the real one, and the window resolves what it is handed against the host's.
Callers join `chat_rel(kind)` onto `current_workspace()` for their own filesystem work and hand the
relative form to everything that leaves the sandbox — artifacts, records, a download's save_path.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_runtime.application.run_context import current_run_context

REFERENCES = "references"
WORKFLOWS = "workflows"
OUTPUTS = "outputs"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def chat_folder(session_key: str | None = None) -> str:
    """The folder name for one chat — the run's own session when none is given."""
    if session_key is None:
        ctx = current_run_context()
        session_key = str(getattr(ctx, "session_key", "") or "") if ctx else ""
    return _UNSAFE.sub("_", session_key or "") or "_"


def chat_rel(kind: str) -> str:
    """`workflows/<chat>` — workspace-relative, posix: the form for everything that NAMES a file."""
    return f"{kind}/{chat_folder()}"


def chat_dir(root: Path, kind: str) -> Path:
    """The same folder as a filesystem path under `root`, the sandbox's own workspace."""
    return root / kind / chat_folder()

"""Which subtrees of the run's workspace a sandboxed plugin's tools read — declared, not guessed.

THE WORKSPACE IS THE ACCOUNT'S, AND IT GROWS. A microVM has no view of the daemon's disk, so the
microvm backend ships the workspace as a zip with every call — and shipped ALL of it: ten chats of
reference photos (118 MB) and every render ever downloaded went up for a node search that reads
none of them. Four of those zips built at once, in memory, is what put a 3.5 GB daemon on the
floor twice in one afternoon.

A plugin says what it reads, in its plugin.toml, beside the hosts it calls and the credential
names it uses:

    [sandbox]
    workspace = [".studio", "workflows/{session}", "references/{session}"]

Each entry is a workspace-RELATIVE posix path — a directory (everything under it ships) or one
file. `{session}` is the run's session key folded to the path-safe form the plugins use for their
per-chat folders (chat_paths.chat_folder: any character outside [A-Za-z0-9._-] becomes `_`); the
two rules must agree, or the plugin looks in a folder that was never shipped. ABSENT MEANS THE
WHOLE WORKSPACE, as before: a plugin that declares nothing has told us nothing, and a tool
silently missing a file it reads is exactly the bug this must not introduce.

Pure: strings in, strings out, no IO.
"""

from __future__ import annotations

import re

SESSION_TOKEN = "{session}"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def session_folder(session_key: str) -> str:
    """The per-chat folder name for a session key — chat_paths.chat_folder's rule, mirrored."""
    return _UNSAFE.sub("_", session_key or "") or "_"


def validate_scopes(declared) -> tuple[str, ...]:
    """The declaration as written, checked: relative, posix, no `..`, no empty parts. Raises
    ValueError naming the bad entry — a wrong declaration fails at load, not on a call."""
    out: list[str] = []
    for raw in declared or ():
        s = str(raw).strip().replace("\\", "/")
        # REFUSED, NOT RELATIVISED: "/etc" stripped to "etc" would be a silent rewrite of what
        # the author declared. A leading slash or a drive letter is an absolute path, whatever
        # the platform.
        if s.startswith("/") or s.split("/", 1)[0].endswith(":"):
            raise ValueError(f"[sandbox] workspace: {raw!r} must be relative to the workspace")
        s = s.rstrip("/")
        if not s:
            raise ValueError("[sandbox] workspace: an empty entry")
        # Split by hand: PurePosixPath swallows "." and "//" segments, and a declaration that
        # needs normalising is one the author should write plainly.
        parts = s.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError(
                f"[sandbox] workspace: {raw!r} must be a plain relative path — no '.', '..' or "
                "empty segments"
            )
        if s not in out:
            out.append(s)
    return tuple(out)


def expand_scopes(declared, session_key: str) -> tuple[str, ...]:
    """The declaration for ONE run: `{session}` filled in with this run's chat folder."""
    folder = session_folder(session_key)
    return tuple(s.replace(SESSION_TOKEN, folder) for s in validate_scopes(declared))


def in_scopes(rel_posix: str, scopes) -> bool:
    """Is a workspace-relative path inside one of the scopes — the scope itself, or under it?"""
    rel = rel_posix.strip("/")
    for scope in scopes or ():
        if rel == scope or rel.startswith(scope + "/"):
            return True
    return False

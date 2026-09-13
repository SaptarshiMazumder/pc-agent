"""Reference SLOTS — the workflow's inputs by ROLE, filled by files, gated at run time.

THE PROBLEM THIS ENDS. References used to arrive as loose uploads announced by a chat message:
the agent had to guess which file was which from its name, could not tell whether it had all of
them, stopped after each one to report the upload, and re-emitted the graph with the filenames
wired in by hand. A job that needed three images and got two ran anyway, or stalled in prose.

THE MECHANISM. A workflow names what it needs by ROLE: a loader node whose `image` (or `video`,
`audio`) input is the token `@model` declares the slot "model". A slot is FILLED by a file in
this chat's references folder whose stem is the role — `references/<chat>/model.jpg` — which the
window writes when the user adds a file to that slot; nobody names anything, and the agent never
needs to see a pixel to know which file is which. `comfy_run` resolves every token against the
folder, uploads the files, substitutes the server names and submits — or REFUSES, naming the empty
slots, and nothing runs. The gate is a file check, not an instruction.

`.slots.json` beside the files records each role's description (from the emit) and the workflows
that use it, so the window can list empty slots — with what they are for — before any file exists.
Per chat by construction: it lives in the chat's own folder.

Every path here is workspace-relative and posix, like the rest of the plugin (see chat_paths).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import chat_paths

TOKEN = "@"
ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
RECORD = ".slots.json"

#: Loader inputs a token may stand in for. Anything else that starts with "@" is a typo we refuse.
_SLOT_FIELDS = frozenset({"image", "video", "audio", "file", "clip", "images"})


def role_of(value) -> str | None:
    """The role a value declares, or None when it is not a token."""
    if isinstance(value, str) and value.startswith(TOKEN) and len(value) > 1:
        return value[1:].strip()
    return None


def roles_in(graph: dict) -> dict[str, list[tuple[str, str]]]:
    """Every slot the graph declares -> the (node, field) pairs that use it."""
    out: dict[str, list[tuple[str, str]]] = {}
    for nid, entry in (graph or {}).items():
        if not isinstance(entry, dict):
            continue
        for field, value in (entry.get("inputs") or {}).items():
            role = role_of(value)
            if role is not None:
                out.setdefault(role, []).append((str(nid), str(field)))
    return out


def bad_roles(graph: dict) -> list[str]:
    """Tokens that cannot be slots: a malformed role, or a token on a non-loader input."""
    problems: list[str] = []
    for role, uses in roles_in(graph).items():
        if not ROLE_RE.match(role):
            problems.append(
                f"'{TOKEN}{role}' is not a valid role — lowercase letters, digits, '-' or '_', "
                "starting with a letter (e.g. @model, @garment, @start_frame)"
            )
        for nid, field in uses:
            if field not in _SLOT_FIELDS:
                problems.append(
                    f"node {nid}.{field} = '{TOKEN}{role}': a slot token belongs on a loader's "
                    f"file input ({', '.join(sorted(_SLOT_FIELDS))}), not on '{field}'"
                )
    return problems


def folder(root: Path) -> Path:
    return chat_paths.chat_dir(root, chat_paths.REFERENCES)


def slot_file(root: Path, role: str) -> Path | None:
    """The file filling `role` — `references/<chat>/<role>.<ext>` — or None."""
    d = folder(root)
    if not d.is_dir():
        return None
    hits = sorted(
        p for p in d.iterdir() if p.is_file() and not p.name.startswith(".") and p.stem == role
    )
    return hits[0] if hits else None


def status(root: Path, roles) -> tuple[dict[str, str], list[str]]:
    """(filled: role -> workspace-relative file, missing: roles with no file)."""
    filled: dict[str, str] = {}
    missing: list[str] = []
    for role in roles:
        p = slot_file(root, role)
        if p is None:
            missing.append(role)
        else:
            filled[role] = p.relative_to(root).as_posix()
    return filled, missing


def _record_path(root: Path) -> Path:
    return folder(root) / RECORD


def declared(root: Path) -> dict[str, dict]:
    """role -> {what, workflows} as recorded by emits in this chat."""
    try:
        data = json.loads(_record_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record(root: Path, workflow: str, roles, whats: dict[str, str]) -> None:
    """Remember that `workflow` uses these roles, with their descriptions. A re-emit that drops a
    role drops it from that workflow; a role no workflow uses any more is forgotten."""
    data = declared(root)
    for role, entry in list(data.items()):
        wfs = [w for w in (entry.get("workflows") or []) if w != workflow]
        if wfs:
            entry["workflows"] = wfs
        else:
            del data[role]
    for role in roles:
        entry = data.setdefault(role, {"what": "", "workflows": []})
        if whats.get(role):
            entry["what"] = whats[role]
        if workflow not in entry["workflows"]:
            entry["workflows"].append(workflow)
    p = _record_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")


def describe(root: Path, roles, whats: dict[str, str] | None = None) -> str:
    """One line per slot for a tool result: role — what — filled (file) | EMPTY."""
    whats = whats or {r: (declared(root).get(r) or {}).get("what", "") for r in roles}
    filled, missing = status(root, roles)
    lines = []
    for role in roles:
        what = f" — {whats[role]}" if whats.get(role) else ""
        state = f"filled ({filled[role]})" if role in filled else "EMPTY"
        lines.append(f"  {TOKEN}{role}{what}: {state}")
    # Files the user added that fill no slot: the one thing a chat nudge can fix
    # ("the second one is the shirt" -> comfy_reference_assign). Named here so the model knows
    # they exist without seeing the panel.
    loose = unassigned(root, roles)
    if loose:
        lines.append(
            "  not in any slot: " + ", ".join(loose)
            + " — comfy_reference_assign(file, role) when the user says which is which"
        )
    return "\n".join(lines)


def unassigned(root: Path, roles) -> list[str]:
    """This chat's reference files whose stem is no declared role."""
    d = folder(root)
    if not d.is_dir():
        return []
    taken = set(roles)
    return sorted(
        p.name for p in d.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.stem not in taken
    )


def bind(graph: dict, server_names: dict[str, str]) -> dict:
    """The graph with every token replaced by the instance-side filename of its slot's file."""
    out = json.loads(json.dumps(graph))
    for nid, entry in out.items():
        if not isinstance(entry, dict):
            continue
        for field, value in list((entry.get("inputs") or {}).items()):
            role = role_of(value)
            if role is not None and role in server_names:
                entry["inputs"][field] = server_names[role]
    return out


def assign(root: Path, filename: str, role: str) -> str:
    """Give the chat's file `filename` the role `role` by renaming it `<role>.<ext>` in place.
    Returns the new workspace-relative path. Refuses anything outside this chat's folder."""
    if not ROLE_RE.match(role):
        raise ValueError(f"'{role}' is not a valid role (lowercase letters, digits, '-', '_')")
    d = folder(root)
    src = d / Path(filename).name
    if not src.is_file():
        have = sorted(p.name for p in d.iterdir() if p.is_file() and not p.name.startswith(".")) if d.is_dir() else []
        raise FileNotFoundError(
            f"'{filename}' is not a file this chat added. This chat's references: "
            + (", ".join(have) or "none")
        )
    dst = d / f"{role}{src.suffix.lower()}"
    if dst != src:
        for old in d.iterdir():  # one file per role: an earlier holder of the role goes
            if old.is_file() and old.stem == role and old != src:
                old.unlink()
        src.replace(dst)
    return dst.relative_to(root).as_posix()

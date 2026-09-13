"""comfy_delete — remove files from THIS chat's folders, at the user's request, with the agent in
the loop.

WHY THE AGENT AND NOT A BUTTON. The window could call the daemon's `workspace.delete` directly —
it is app-callable and the reference "Replace" flow already does. It deliberately does not for
anything else: a file in this workspace is usually LOAD-BEARING for the job in progress. A
reference fills a slot that comfy_run re-checks on every submit; a workflow's validation is what
authorises its installs; a render may be the next workflow's input. A button cannot know any of
that. The agent can, and the user talking to the agent is how the intent gets recorded in the one
place the rest of the job reads — the conversation. So the window SELECTS and ASKS; this tool
DELETES, and refuses when it should.

REFUSE-UNLESS-FORCE IS THE SAFETY NET, and it is grounded in checks rather than in the model's
judgement: bound slot, armed validation, recorded download. A refusal names the reason so the
agent can relay it in one line; an explicit "yes, delete it" comes back as `force=true`. There is
no trash and no undo behind this — the refusal is the whole margin.

FENCED BY CONSTRUCTION. Only `references/<chat>`, `workflows/<chat>` and `outputs/<chat>` under the
run's workspace — the same three folders the window lists — resolved and checked for containment
before anything is touched. Never `.studio/` (the job's gate records), never another chat's folder,
never outside the workspace. A sandboxed copy of this plugin could not reach further anyway; the
check is here so the trusted copy cannot either.

GATE RECORDS FOLLOW THE FILE. Deleting a validated workflow also drops its validation; deleting a
downloaded render drops it from the download record. Left behind, those records would keep
authorising an install for a file that is gone, and keep offering a render that cannot be sent.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import chat_paths
import reference_slots
import studio_state

#: The only folders a delete may touch. Anything else is refused by name, force or not.
_KINDS = (chat_paths.REFERENCES, chat_paths.WORKFLOWS, chat_paths.OUTPUTS)


def _workflow_name(path: Path) -> str:
    """`storyboard.api.json` / `storyboard.json` -> `storyboard`: the key every per-workflow
    record uses (same rule as comfy_bridge._workflow_name)."""
    base = path.name
    if base.endswith(".api.json"):
        return base[:-9]
    return base[:-5] if base.endswith(".json") else base


def _resolve(ws: Path, raw: str) -> tuple[Path | None, str]:
    """A requested path -> (absolute file inside one of this chat's folders, or None, why).

    Accepts the workspace-relative form the window and the announce use, or an absolute path
    the model copied out of an artifact — either way it must land inside one of the three chat
    folders once resolved, or it is not ours to delete."""
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        return None, "empty path"
    p = Path(text)
    target = (p if p.is_absolute() else ws / p).resolve()
    root = ws.resolve()
    for kind in _KINDS:
        folder = chat_paths.chat_dir(root, kind).resolve()
        try:
            rel = target.relative_to(folder)
        except ValueError:
            continue
        if not rel.parts:
            return None, f"{text} is the {kind} folder itself, not a file in it"
        if rel.parts[0].startswith("."):
            return None, f"{text} is a record the job depends on, not a file to delete"
        return target, ""
    return None, (
        f"{text} is outside this chat's references/, workflows/ and outputs/ folders — "
        "this tool never deletes anywhere else"
    )


def _load_bearing(ws: Path, target: Path) -> str:
    """Why deleting `target` would break something in progress — '' when it would not."""
    kind = target.parent.parent.name
    if kind == chat_paths.REFERENCES:
        role = target.stem
        entry = reference_slots.declared(ws).get(role)
        if entry is not None:
            wfs = ", ".join(entry.get("workflows") or []) or "a workflow"
            return f"@{role} is a reference slot that {wfs} reads on every run — the next comfy_run would refuse until it is filled again"
        return ""
    if kind == chat_paths.WORKFLOWS:
        name = _workflow_name(target)
        if name in studio_state.validated_names():
            return f"{name} was validated in this conversation and its install gate is armed — deleting it orphans that authorisation"
        return ""
    if kind == chat_paths.OUTPUTS:
        rel = target.relative_to(ws.resolve()).as_posix()
        if rel in studio_state.downloaded_in_session():
            return f"{rel} was rendered in this conversation and may be a later workflow's input"
        return ""
    return ""


def _forget(ws: Path, target: Path) -> None:
    """Keep the job's records honest about a file that is no longer there."""
    kind = target.parent.parent.name
    if kind == chat_paths.WORKFLOWS:
        studio_state.forget_validated(_workflow_name(target))
    elif kind == chat_paths.OUTPUTS:
        studio_state.forget_downloaded(target.relative_to(ws.resolve()).as_posix())


class ComfyDeleteTool(Tool):
    name = "comfy_delete"
    label = "Delete files from this chat's workspace"
    default_retryable = False
    description = (
        "Delete files from THIS chat's references/, workflows/ or outputs/ folders — ONLY when the "
        "user has asked for it (the window sends 'Please delete …' naming the paths). Pass the "
        "paths as given. If a file is load-bearing for the job — a reference bound to a slot, a "
        "workflow whose validation is armed, a render this conversation produced — the tool "
        "REFUSES it and says why; relay that reason in one line and ask once. Only an explicit "
        "yes from the user is `force=true`. Never delete anything the user did not name, never "
        "call this to tidy up on your own, and never reach outside those three folders (it will "
        "not let you). There is no undo."
    )
    parameters = {
        "type": "object",
        "required": ["paths"],
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The files to delete, exactly as the user's request names them (workspace-relative, e.g. 'outputs/chat-…/front_00001_.png').",
            },
            "force": {
                "type": "boolean",
                "description": "True ONLY after the user has been told a file is load-bearing and has said to delete it anyway.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        raw_paths = params.get("paths") or []
        force = bool(params.get("force"))
        if not isinstance(raw_paths, list) or not raw_paths:
            return ToolResult.text("comfy_delete: no paths given", is_error=True)
        try:
            ws = Path(current_workspace(".") or ".")
            deleted: list[str] = []
            refused: list[dict] = []
            missing: list[str] = []
            for raw in raw_paths:
                target, why = _resolve(ws, str(raw))
                if target is None:
                    refused.append({"path": str(raw), "reason": why})
                    continue
                if not target.is_file():
                    missing.append(str(raw))
                    continue
                reason = "" if force else _load_bearing(ws, target)
                if reason:
                    refused.append({"path": str(raw), "reason": reason})
                    continue
                target.unlink()
                _forget(ws, target)
                deleted.append(target.relative_to(ws.resolve()).as_posix())

            lines: list[str] = []
            if deleted:
                lines.append(f"deleted {len(deleted)} file(s):")
                lines += [f"  {p}" for p in deleted]
            if missing:
                lines.append(f"already gone ({len(missing)}): " + ", ".join(missing))
            if refused:
                lines.append(
                    f"NOT deleted ({len(refused)}) — tell the user why, in one line each, and ask "
                    "once; delete only on an explicit yes, with force=true:"
                )
                lines += [f"  {r['path']}: {r['reason']}" for r in refused]
            if not lines:
                lines.append("nothing to do")
            return ToolResult.text(
                "\n".join(lines),
                details={"deleted": deleted, "refused": refused, "missing": missing},
                is_error=not deleted and not missing and bool(refused),
            )
        except Exception as e:  # noqa: BLE001 — never let a tool crash the loop
            return ToolResult.text(f"comfy_delete failed: {type(e).__name__}: {e}", is_error=True)

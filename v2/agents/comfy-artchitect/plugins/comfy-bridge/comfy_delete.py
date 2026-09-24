"""comfy_delete — APPROVE the removal of files from THIS chat's folders, at the user's request,
with the agent in the loop. The window performs the removal.

WHY THIS TOOL DOES NOT UNLINK ANYTHING. This plugin runs sandboxed (comfy-artchitect arrives as
a .agentpkg, so classify_origin calls it untrusted), and a sandbox is handed a COPY of the
workspace: creations and edits sync back as a change set, and — deliberately, see the header of
infrastructure/tools/sandbox/microvm_backend.py — "deletions never propagate, an untrusted tool
must not be able to erase a workspace through the sync channel". So an unlink here removed the
file from a copy that is thrown away, reported honest success, and left the real file untouched;
the next call shipped a fresh zip and the file was back. That is exactly what it did: three
files "deleted" twice over, still listed in the panel, and nothing anywhere saying why.

So the split follows the trust boundary instead of fighting it. THIS TOOL DECIDES — it owns the
checks, which are the part that needs the workspace and the job's records — and hands back the
approved paths in `details`. THE WINDOW DELETES, through the daemon's `workspace.delete`, which
is app-callable and is already how the reference Replace flow removes the file it replaces. The
host performs the destructive act; the sandbox only ever says what should happen.

WHY THE AGENT AND NOT A BUTTON. The window could call the daemon's `workspace.delete` directly —
it is app-callable and the reference "Replace" flow already does. It deliberately does not for
anything else: a file in this workspace is usually LOAD-BEARING for the job in progress. A
reference fills a slot that comfy_run re-checks on every submit; a workflow's validation is what
authorises its installs; a render may be the next workflow's input. A button cannot know any of
that. The agent can, and the user talking to the agent is how the intent gets recorded in the one
place the rest of the job reads — the conversation. So the window SELECTS and ASKS; this tool
DELETES, and refuses when it should.

ONE WARNING, THEN GONE. This used to refuse a file that was load-bearing — a reference in a slot, a
validated workflow, a render — until the user said yes a second time. That was a negotiation in
place of a delete: select, ask, get refused, say yes, get it deleted. The window now warns once
("deleting files can break workflows that use them") and deletes; this tool, for the case where
the person asks in the conversation, does the same. `force` is accepted and means nothing.

FENCED BY CONSTRUCTION. Only `references/<chat>`, `workflows/<chat>` and `outputs/<chat>` under the
run's workspace — the same three folders the window lists — resolved and checked for containment
before a path is ever approved. Never `.studio/` (the job's gate records), never another chat's
folder, never outside the workspace. The window deletes the approved list and nothing else, so
this check is still the whole fence — it just guards an instruction now instead of an unlink.

GATE RECORDS FOLLOW THE FILE. Approving a validated workflow's removal also drops its validation;
approving a downloaded render drops it from the download record. Left behind, those records would
keep authorising an install for a file that is going, and keep offering a render that cannot be
sent. These are WRITES, so unlike the unlink they do reach the daemon's copy.
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
        "Approve deletion of files from THIS chat's references/, workflows/ or outputs/ folders "
        "— ONLY when the user has asked for it (the window sends 'Please delete …' naming the "
        "paths). Pass the paths as given. The window removes what this approves, within a "
        "second, so tell the user the files are gone — never that they are queued or pending. "
        "The only refusal is a path outside those three folders; relay it in one line. Never "
        "name a file the user did not, never call this to tidy up on your own. No undo."
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
                "description": "Accepted for compatibility; nothing needs it any more.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        raw_paths = params.get("paths") or []
        if not isinstance(raw_paths, list) or not raw_paths:
            return ToolResult.text("comfy_delete: no paths given", is_error=True)
        try:
            ws = Path(current_workspace(".") or ".")
            approved: list[str] = []
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
                # NO unlink — see the header. This side's copy is discarded, so removing the
                # file here would hide it from the rest of THIS run while the real one stayed.
                # The records do move now, because writes, unlike deletions, sync back.
                _forget(ws, target)
                approved.append(target.relative_to(ws.resolve()).as_posix())

            lines: list[str] = []
            if approved:
                lines.append(f"deleted {len(approved)} file(s):")
                lines += [f"  {p}" for p in approved]
            if missing:
                lines.append(f"already gone ({len(missing)}): " + ", ".join(missing))
            if refused:
                lines.append(
                    f"NOT deleted ({len(refused)}) — outside this chat's folders; tell the user "
                    "why, in one line each:"
                )
                lines += [f"  {r['path']}: {r['reason']}" for r in refused]
            if not lines:
                lines.append("nothing to do")
            # `approved` IS THE INSTRUCTION, and the window reads it from HERE rather than from
            # the prose above (app/src/agentd/run-events.ts). Workspace-relative posix — the one
            # form that means the same file on both sides of the sandbox.
            return ToolResult.text(
                "\n".join(lines),
                details={"approved": approved, "refused": refused, "missing": missing},
                is_error=not approved and not missing and bool(refused),
            )
        except Exception as e:  # noqa: BLE001 — never let a tool crash the loop
            return ToolResult.text(f"comfy_delete failed: {type(e).__name__}: {e}", is_error=True)

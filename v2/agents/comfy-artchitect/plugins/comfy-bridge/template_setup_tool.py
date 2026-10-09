"""template_setup — make Comfy Cloud ready for a template, from its guide, with no guessing.

WHAT IT DOES. The template's steps are in this chat (template_use put them there, with the
template's `guide.json`). This reads Comfy Cloud's node list once, and for every step:
  • a model file Comfy Cloud lacks → imported from the guide's link (comfy_install: Comfy Cloud's
    own import, its progress, its gates);
  • a node class Comfy Cloud lacks → a GAP, said in words: Comfy Cloud runs only its preinstalled
    node packs, so nothing here installs one (version-1 templates installed packs on a machine of
    one's own; that is gone).
Then it answers with what is ready and — only if there is any — what the guide did not cover.

It records each step as validated with exactly the files Comfy Cloud lacks, which is what
comfy_install requires — the record comfy_validate would have written, from the guide.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import chat_paths
import studio_state
from model_readiness import ModelReadiness
from template_guide import FILE, TemplateGuide


class TemplateSetupTool(Tool):
    name = "template_setup"
    label = "Set up a template"
    # Imports are long: the same declared wait as comfy_install, whose work this drives.
    default_timeout_sec = 840.0
    default_retryable = True
    default_retry_on_timeout = True
    default_max_retries = 4
    description = (
        "After template_use: make Comfy Cloud ready for the template — imports the model files it "
        "lacks, from the links in the template's guide. Call it once, before comfy_run; call again "
        "after a timeout (finished imports are not redone). It answers with what is ready and, only "
        "if any, the GAPS the guide does not cover."
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, *, object_info, install_models) -> None:
        """:param object_info: () -> Comfy Cloud's /api/object_info (raises when unreachable).
        :param install_models: async (files, abort, on_update) -> ToolResult (comfy_install)."""
        self._object_info = object_info
        self._install_models = install_models

    async def execute(self, tool_call_id, params, abort, on_update=None):
        ws = Path(current_workspace(".") or ".")
        folder = chat_paths.chat_dir(ws, chat_paths.WORKFLOWS)
        guide, why = TemplateGuide.load(folder)
        if guide is None:
            return ToolResult.text(f"template_setup: this chat has no version-2 template guide — {why}. "
                                   "template_use a template first.", is_error=True)
        graphs = {}
        for role in sorted(studio_state.template_steps()):
            path = folder / f"{role}.api.json"
            if path.is_file():
                graphs[role] = json.loads(path.read_text(encoding="utf-8"))
        if not graphs:
            return ToolResult.text("template_setup: no template step is in this chat — call template_use first.",
                                   is_error=True)
        try:
            info = self._object_info()
        except (ValueError, OSError) as e:
            return ToolResult.text(f"template_setup: could not read Comfy Cloud's nodes: {e}", is_error=True)

        gaps: list[str] = []
        files: dict[str, dict] = {}
        readiness = ModelReadiness(info)
        for role, graph in graphs.items():
            lacking = sorted({n["class_type"] for n in graph.values() if isinstance(n, dict)} - set(info))
            gaps += [f"node {c} (step {role}) is not on Comfy Cloud" for c in lacking]
            wanted = []
            for name in [m.rsplit(" (node ", 1)[0] for m in readiness.missing(graph)]:
                source = guide.model_for(name)
                if source is None:
                    gaps.append(f"model {name} (step {role})")
                    continue
                wanted.append(source.filename)
                files[source.filename] = {"filename": source.filename, "url": source.url, "kind": source.kind}
            studio_state.mark_validated(role, wanted, lacking)
        if files:
            result = await self._install_models(list(files.values()), abort, on_update)
            if result.is_error:
                return ToolResult.text("template_setup: importing the models failed — " + _text(result),
                                       is_error=True)
        ready = f"{len(graphs)} step(s): " + ", ".join(graphs)
        if gaps:
            return ToolResult.text(
                f"template_setup: set up what the guide covers for {ready}. NOT covered by the template's "
                f"guide ({FILE}) — tell the user in one line; a missing node means the template cannot run "
                "on Comfy Cloud as it is:\n  " + "\n  ".join(gaps),
                details={"gaps": gaps, "installed_models": list(files)},
            )
        return ToolResult.text(
            f"template_setup: Comfy Cloud is ready for {ready} — {len(files)} model(s) imported, every node "
            "present. Next: the settings (template_set), then comfy_run each step in order.",
            details={"gaps": [], "installed_models": list(files)},
        )


def _text(result: ToolResult) -> str:
    return "".join(getattr(b, "text", "") for b in result.content)[:600]

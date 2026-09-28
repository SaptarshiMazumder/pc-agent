"""template_setup — set the machine up for a template, from its setup guide, with no guessing.

WHAT IT DOES. The template's steps are in this chat (template_use put them there, with the
template's `setup.json`). This reads the machine once, and for every step:
  • a node class the machine lacks → the guide's pack that provides it (or, for packs the guide
    names without their classes, every such pack) is installed from its repository;
  • a model file the machine lacks → the guide's link for it is downloaded.
Then it answers with what is ready and — only if there is any — what the guide did not cover.
Those GAPS are the one thing the agent works out itself; everything else was decided when the
template was saved.

THE SAME INSTALLERS AS EVER, AND NEVER COMFYUI-MANAGER. Node packs install straight from the
repositories the guide names, all in one job on the machine (clones at once, one ComfyUI restart
for all) — Manager adds nothing once the link is known, and failed a template outright with an
HTTP 500. Models go through comfy_install (our parallel downloader, its progress, its gates). What it adds is the authority: a template's step is recorded as validated with
exactly the files and classes the machine lacks, which is what those two tools require — the
same record comfy_validate would have written, taken from the guide instead of a guess.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import chat_paths
import studio_state
from model_readiness import ModelReadiness
from template_setup_guide import FILE, TemplateSetupGuide


class TemplateSetupTool(Tool):
    name = "template_setup"
    label = "Set up a template"
    # Downloads are long: the same declared wait as comfy_install, whose work this drives.
    default_timeout_sec = 840.0
    default_retryable = True
    default_retry_on_timeout = True
    default_max_retries = 4
    description = (
        "After template_use: set the machine up for the template from its setup guide — installs "
        "the node packs and downloads the models its steps need, from the links the template "
        "carries. Call it once, before comfy_run; call again after a timeout (finished work is "
        "not redone). It answers with what is ready and, only if any, the GAPS the guide does not "
        "cover — work out only those."
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, *, object_info, install_packs, install_models) -> None:
        """:param object_info: () -> the machine's /api/object_info (raises when unreachable).
        :param install_packs: async (repositories, abort, on_update) -> ToolResult — every pack
            from its repository in one job, one restart.
        :param install_models: async (files, abort, on_update) -> ToolResult (comfy_install)."""
        self._object_info = object_info
        self._install_packs = install_packs
        self._install_models = install_models

    async def execute(self, tool_call_id, params, abort, on_update=None):
        ws = Path(current_workspace(".") or ".")
        folder = chat_paths.chat_dir(ws, chat_paths.WORKFLOWS)
        guide, why = TemplateSetupGuide.load(folder)
        if guide is None:
            return ToolResult.text(
                f"template_setup: this chat's template has no usable setup guide — {why}. Set its "
                "steps up the normal way: comfy_validate each, install what it names.",
                is_error=True,
            )
        steps = sorted(studio_state.template_steps())
        graphs = {}
        for role in steps:
            path = folder / f"{role}.api.json"
            if path.is_file():
                graphs[role] = json.loads(path.read_text(encoding="utf-8"))
        if not graphs:
            return ToolResult.text("template_setup: no template step is in this chat — call template_use first.",
                                   is_error=True)
        try:
            info = self._object_info()
        except (ValueError, OSError) as e:
            return ToolResult.text(f"template_setup: could not read the machine's nodes: {e}", is_error=True)

        gaps: list[str] = []
        # ── node packs ───────────────────────────────────────────────────────────────────
        lacking = {role: sorted({n["class_type"] for n in g.values() if isinstance(n, dict)} - set(info))
                   for role, g in graphs.items()}
        classes = sorted({c for cs in lacking.values() for c in cs})
        if classes:
            # A pack that names its classes is installed when one of them is missing; one that
            # does not (an installer list's entry) is installed whenever anything is missing —
            # installing a pack already there only re-checks its requirements.
            packs = [p for p in guide.node_packs if not p.classes or set(p.classes) & set(classes)]
            for role, cs in lacking.items():
                studio_state.mark_validated(role, [], cs)
            if packs:
                result = await self._install_packs([p.repository for p in packs], abort, on_update)
                if result.is_error:
                    return ToolResult.text("template_setup: installing the node packs failed — "
                                           + _text(result), is_error=True)
            info = self._object_info()
            gaps += [f"node pack for {c}" for c in sorted(set(classes) - set(info))]
        # ── models ───────────────────────────────────────────────────────────────────────
        files: dict[str, dict] = {}
        readiness = ModelReadiness(info)
        for role, graph in graphs.items():
            if any(n.get("class_type") not in info for n in graph.values() if isinstance(n, dict)):
                continue  # its nodes are a gap already; its models are judged once they load
            names = [m.rsplit(" (node ", 1)[0] for m in readiness.missing(graph)]
            wanted = []
            for name in names:
                source = next((m for m in guide.models
                               if m.filename.rsplit("/", 1)[-1].lower() == name.replace("\\", "/").rsplit("/", 1)[-1].lower()),
                              None)
                if source is None:
                    gaps.append(f"model {name} (step {role})")
                    continue
                wanted.append(source.filename)
                files[source.filename] = {"filename": source.filename, "url": source.url, "kind": source.kind}
            studio_state.mark_validated(role, wanted, lacking.get(role, []))
        if files:
            result = await self._install_models(list(files.values()), abort, on_update)
            if result.is_error:
                return ToolResult.text("template_setup: downloading the models failed — " + _text(result),
                                       is_error=True)
        ready = f"{len(graphs)} step(s): " + ", ".join(graphs)
        if gaps:
            return ToolResult.text(
                f"template_setup: set up what the guide covers for {ready}. NOT in the template's "
                f"setup guide ({FILE}) — work out ONLY these, then comfy_run:\n  " + "\n  ".join(gaps),
                details={"gaps": gaps, "installed_models": list(files)},
            )
        return ToolResult.text(
            f"template_setup: the machine is ready for {ready} — "
            f"{len(files)} model(s) downloaded, every node present. NOW comfy_run each step in order, as it is.",
            details={"gaps": [], "installed_models": list(files)},
        )


def _text(result: ToolResult) -> str:
    return "".join(getattr(b, "text", "") for b in result.content)[:600]

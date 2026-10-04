"""pipeline_provision — Phase 2: bring the GPU up to the approved design, and nothing more.

For every stage: validate it LIVE on the box (comfy_validate — which also arms the install gate with
exactly that stage's missing files), then install what is missing in ONE pass — the node packs a
recipe names first, then every missing model file with its URL and folder from the knowledge base,
never guessed — and validate again. Done when every stage compiles on the box.

The GPU itself is started by `gpu_ensure` (the vast-bridge plugin); this tool says so when the box
is not reachable. Nothing here chooses a model: the design did.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import studio_state
from model_download_request import ModelDownloadRequest
from pipeline import Pipeline
from pipeline_tool_context import PipelineToolContext
from pipeline_validator import DISK_GB, DISK_HEADROOM_GB

#: (workflow_path, abort, on_update) -> ToolResult of comfy_validate
Validate = Callable[[str, object, object], Awaitable[ToolResult]]
#: (files=[{filename, url, kind}], abort, on_update) -> ToolResult of comfy_install
InstallModels = Callable[[list, object, object], Awaitable[ToolResult]]
#: (repos=[git urls], abort, on_update) -> ToolResult of the node-pack installer
InstallPacks = Callable[[list, object, object], Awaitable[ToolResult]]



@dataclass
class _LiveState:
    missing: dict[str, list[str]] = field(default_factory=dict)  # stage -> missing model files
    unknown: dict[str, list[str]] = field(default_factory=dict)  # stage -> node classes the box lacks
    # stage -> the box's own report, when it refuses the stage for anything else (a value it does
    # not accept, a rule it breaks): nothing to install, but the stage does not run either.
    broken: dict[str, str] = field(default_factory=dict)
    packs: set[str] = field(default_factory=set)  # repos the recipes need
    unreachable: str = ""

    @property
    def ready(self) -> bool:
        return (not self.unreachable and not any(self.missing.values()) and not any(self.unknown.values())
                and not self.broken)


class PipelineProvisionTool(Tool):
    name = "pipeline_provision"
    label = "Set up the GPU for the design"
    default_retryable = True
    default_retry_on_timeout = True
    description = (
        "After the user approved the card: check every stage on the running GPU and install exactly "
        "what is missing — node packs, then model files with their links from the knowledge base — "
        "then check again. Returns when every stage is ready to run, or what is still missing and "
        "why. If the GPU is not up yet, call gpu_ensure first."
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, validate: Validate, install_models: InstallModels, install_packs: InstallPacks,
                 timeout_s: float, max_retries: int, own_machine: Callable[[], bool],
                 context: Callable[[], PipelineToolContext] | None = None) -> None:
        self._validate = validate
        self._install_models = install_models
        self._install_packs = install_packs
        # The person's own ComfyUI (their Vast machine, or a link they gave): nothing to start, and
        # its disk is theirs — the rented GPU's disk size says nothing about it.
        self._own_machine = own_machine
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))
        # A model download is minutes: the same declared wait as comfy_install, whose call this holds.
        self.default_timeout_sec = timeout_s
        self.default_max_retries = max_retries

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            ctx = self._context()
            pipeline = ctx.store.load()
            if pipeline is None:
                return ToolResult.text("no pipeline in this chat yet — pipeline_plan first", is_error=True)
            refused = studio_state.design_approved([s.name for s in pipeline.stages])
            if refused:
                return ToolResult.text(refused, is_error=True)
            own = self._own_machine()
            state = await self._check(ctx, pipeline, abort)
            if state.unreachable:
                return self._unreachable(own, state.unreachable)
            lines = []
            packs = sorted(state.packs) if any(state.unknown.values()) else []
            if packs:
                res = await self._install_packs(packs, abort, on_update)
                lines.append(f"node packs {', '.join(packs)}: " + ("FAILED" if res.is_error else "installed"))
                if res.is_error:
                    return ToolResult.text("\n".join(lines) + "\n" + res.content[0].text, is_error=True)
                # PACKS FIRST, THEN LOOK AGAIN: a pack's own loaders list their model folders only once
                # the pack is loaded, so the files they need show up as missing only now.
                state = await self._check(ctx, pipeline, abort)
                if state.unreachable:
                    return self._unreachable(own, state.unreachable)
            files, unsourced = self._install_list(ctx, pipeline, state.missing)
            need_gb = sum(ctx.catalog.file_bytes(f["filename"]) or 0 for f in files) / 1e9
            if not own and need_gb > DISK_GB - DISK_HEADROOM_GB:
                # Said BEFORE a byte moves: a disk that fills halfway leaves a box with half a design.
                return ToolResult.text(
                    f"the missing models come to {need_gb:.1f} GB; the rented GPU keeps "
                    f"{DISK_GB - DISK_HEADROOM_GB:.0f} GB for models. Nothing was downloaded. Tell the user "
                    "plainly: this design needs a GPU with a bigger disk — it is their decision. Do not "
                    "change the design to fit.", is_error=True)
            if unsourced:
                lines.append("not in the knowledge base, so not installed here (find a direct link with "
                             "comfy_research, then comfy_install them): " + ", ".join(sorted(unsourced)))
            if files:
                res = await self._install_models(files, abort, on_update)
                lines.append(res.content[0].text)
                if res.is_error:
                    return ToolResult.text("\n".join(lines), is_error=True)
            if files:
                state = await self._check(ctx, pipeline, abort)
            if state.ready:
                lines.append(f"ready: all {len(pipeline.stages)} stage(s) compile on the GPU with every model "
                             "in place. Next: pipeline_run.")
            else:
                if any(state.missing.values()):
                    lines.append("still missing: " + ", ".join(sorted({f for v in state.missing.values() for f in v})))
                if any(state.unknown.values()):
                    lines.append("node classes the GPU does not have: "
                                 + ", ".join(sorted({c for v in state.unknown.values() for c in v})))
                for name, report in state.broken.items():
                    lines.append(f"stage {name} does not compile on this GPU, with nothing left to install:\n{report}")
            return ToolResult.text("\n".join(lines), details={"ready": state.ready}, is_error=not state.ready)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"pipeline_provision failed: {type(e).__name__}: {e}", is_error=True)

    @staticmethod
    def _unreachable(own: bool, why: str) -> ToolResult:
        if own:
            return ToolResult.text(
                "the person's own ComfyUI did not answer — nothing here can start it. Ask them to check that "
                f"it is running and that the link still works, then call pipeline_provision again.\n  {why}",
                is_error=True)
        return ToolResult.text(f"the GPU is not reachable — call gpu_ensure, then pipeline_provision again.\n  {why}",
                               is_error=True)

    # ------------------------------------------------------------------ live check

    async def _check(self, ctx: PipelineToolContext, pipeline: Pipeline, abort) -> _LiveState:
        state = _LiveState()
        for stage in pipeline.stages:
            res = await self._validate(ctx.store.rel(f"{stage.name}.api.json"), abort, None)
            text = res.content[0].text if res.content else ""
            # comfy_validate says "without a GPU" exactly when no box answered; any other refusal
            # that is not a compile report (a refused credential, a transport error) is the box too.
            offline = "without a gpu" in text.lower()
            failed = res.is_error and "does NOT compile" not in text
            if offline or failed:
                state.unreachable = text.splitlines()[0] if text else "no answer from the GPU"
                return state
            rec = studio_state.validation(stage.name)
            state.missing[stage.name] = list(rec.get("missing_files") or [])
            state.unknown[stage.name] = list(rec.get("unknown_classes") or [])
            if res.is_error and not state.missing[stage.name] and not state.unknown[stage.name]:
                state.broken[stage.name] = text
            if not stage.custom:
                state.packs.update(ctx.builder.recipe_of(stage).pack_links)
        return state

    @staticmethod
    def _install_list(ctx: PipelineToolContext, pipeline: Pipeline,
                      missing: dict[str, list[str]]) -> tuple[list[dict], list[str]]:
        """[{filename, url, kind}] for every missing file the knowledge base describes (once each),
        and the names it does not. The stage's OWN family is asked first: one file name can sit in
        two families under different folders (a FLUX checkpoint the controlnet family also lists),
        and the wrong folder makes the file "installed" yet never loadable — a loop."""
        files, unsourced, seen = [], [], set()
        for stage_name, names in missing.items():
            stage = pipeline.stage(stage_name)
            own = ctx.catalog.families.get(stage.family) if stage is not None and stage.family else None
            for name in names:
                base = name.replace("\\", "/").rsplit("/", 1)[-1]
                if base in seen:
                    continue
                seen.add(base)
                rec = own.file(base) if own is not None else None
                if rec is None:
                    found = ctx.catalog.describe_file(base)
                    rec = found[1] if found else None
                if not rec or not rec.get("url") or not rec.get("folder"):
                    unsourced.append(base)
                    continue
                if ModelDownloadRequest.folder_of(str(rec["folder"])) is None:
                    unsourced.append(f"{base} (its node pack sets it up: {rec['folder']})")
                    continue
                files.append({"filename": base, "url": str(rec["url"]), "kind": str(rec["folder"])})
        return files, unsourced


__all__ = ["PipelineProvisionTool"]

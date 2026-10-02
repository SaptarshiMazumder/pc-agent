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
    packs: set[str] = field(default_factory=set)  # repos the recipes need
    unreachable: str = ""

    @property
    def ready(self) -> bool:
        return not self.unreachable and not any(self.missing.values()) and not any(self.unknown.values())


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
                 timeout_s: float, max_retries: int,
                 context: Callable[[], PipelineToolContext] | None = None) -> None:
        self._validate = validate
        self._install_models = install_models
        self._install_packs = install_packs
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
            unshown = [s.name for s in pipeline.stages if s.name not in studio_state.presented_stages()]
            if unshown:
                return ToolResult.text(
                    f"the design was never put to the person as a whole ({', '.join(unshown)} not on an approval "
                    "card): pipeline_present, then ask_user with exactly its arguments, and wait for the answer.",
                    is_error=True)
            state = await self._check(ctx, pipeline, abort)
            if state.unreachable:
                return ToolResult.text("the GPU is not reachable — call gpu_ensure, then pipeline_provision "
                                       f"again.\n  {state.unreachable}", is_error=True)
            lines = []
            packs = sorted(state.packs) if any(state.unknown.values()) else []
            if packs:
                res = await self._install_packs(packs, abort, on_update)
                lines.append(f"node packs {', '.join(packs)}: " + ("FAILED" if res.is_error else "installed"))
                if res.is_error:
                    return ToolResult.text("\n".join(lines) + "\n" + res.content[0].text, is_error=True)
            files, unsourced = self._install_list(ctx, state.missing)
            need_gb = sum(int((ctx.catalog.describe_file(f["filename"]) or (None, {}))[1].get("bytes") or 0)
                          for f in files) / 1e9
            if need_gb > DISK_GB - DISK_HEADROOM_GB:
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
            if packs or files:
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
            return ToolResult.text("\n".join(lines), details={"ready": state.ready}, is_error=not state.ready)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"pipeline_provision failed: {type(e).__name__}: {e}", is_error=True)

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
            if not stage.custom:
                for p in ctx.builder.recipe_of(stage).packs:
                    state.packs.add(str(p.get("repo") if isinstance(p, dict) else p))
        return state

    @staticmethod
    def _install_list(ctx: PipelineToolContext, missing: dict[str, list[str]]) -> tuple[list[dict], list[str]]:
        """[{filename, url, kind}] for every missing file the knowledge base describes (once each),
        and the names it does not."""
        files, unsourced, seen = [], [], set()
        for names in missing.values():
            for name in names:
                base = name.replace("\\", "/").rsplit("/", 1)[-1]
                if base in seen:
                    continue
                seen.add(base)
                found = ctx.catalog.describe_file(base)
                rec = found[1] if found else None
                if not rec or not rec.get("url") or not rec.get("folder"):
                    unsourced.append(base)
                    continue
                files.append({"filename": base, "url": str(rec["url"]), "kind": str(rec["folder"])})
        return files, unsourced


__all__ = ["PipelineProvisionTool"]

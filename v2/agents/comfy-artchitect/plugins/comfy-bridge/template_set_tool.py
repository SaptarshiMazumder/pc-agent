"""template_set — change a template's step through the settings its guide names, and nothing else.

A version-2 template is a finished, tested setup. What a run may change is written in its guide
(TemplateGuide.settings): a prompt, a seed, a storyboard. This tool is that list and only that list —
a setting the guide does not name is refused, so the agent never re-designs a template to make a
run fit. Called with no settings, it shows each setting's current value (a storyboard's segments,
in order) to rewrite from.

The step stays the template's: setting what the guide allows is how a template is meant to be
used, so the run still needs no ask (studio_state.template_steps).
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import chat_paths
from ltx_director_story import LtxDirectorStory
from template_guide import GuideSetting, TemplateGuide


class TemplateSetTool(Tool):
    name = "template_set"
    label = "Change a template's settings"
    default_retryable = True
    description = (
        "After template_use (version-2 templates): change ONLY the settings the template's guide lists "
        "(template_use shows them). With no `settings`, shows each one's current value. An "
        "`ltx_director` setting takes {\"prompts\": [one per segment, in order], \"frames\": [optional, "
        "one length per segment]} and keeps the storyboard's keyframes. Anything else the guide does "
        "not name is refused — a template is changed only through its settings."
    )
    parameters = {
        "type": "object",
        "properties": {
            "settings": {
                "type": "object",
                "description": '{"<setting name>": value, …} — e.g. {"story": {"prompts": [...]}, "seed": 7}.',
            },
        },
    }

    def _execute(self, ws: Path, params: dict) -> ToolResult:
        folder = chat_paths.chat_dir(ws, chat_paths.WORKFLOWS)
        guide, why = TemplateGuide.load(folder)
        if guide is None:
            return ToolResult.text(f"template_set: this chat has no version-2 template ({why}) — template_use first.",
                                   is_error=True)
        wanted = params.get("settings") or {}
        if not isinstance(wanted, dict):
            return ToolResult.text('template_set: `settings` is an object: {"<name>": value}', is_error=True)
        unknown = [k for k in wanted if guide.setting(k) is None]
        if unknown:
            return ToolResult.text(
                f"template_set: not a setting of this template: {', '.join(unknown)}. Its settings:\n"
                + guide.settings_text() + "\nA template is changed only through these.", is_error=True)
        graphs: dict[str, dict] = {}
        lines: list[str] = []
        try:
            for name, value in wanted.items():
                s = guide.setting(name)
                graph = graphs.get(s.step) or self._graph(folder, s.step)
                graphs[s.step] = graph
                lines.append(self._apply(graph, s, value))
            if not wanted:
                for s in guide.settings:
                    graph = graphs.get(s.step) or self._graph(folder, s.step)
                    graphs[s.step] = graph
                    lines.append(self._show(graph, s))
        except ValueError as e:
            return ToolResult.text(f"template_set: {e}", is_error=True)
        if wanted:
            for step, graph in graphs.items():
                (folder / f"{step}.api.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
            return ToolResult.text("template_set: set\n" + "\n".join(lines))
        return ToolResult.text("template_set: current settings\n" + "\n".join(lines))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        return self._execute(Path(current_workspace(".") or "."), params)

    @staticmethod
    def _graph(folder: Path, step: str) -> dict:
        p = folder / f"{step}.api.json"
        if not p.is_file():
            raise ValueError(f"step {step} is not in this chat — template_use brings it in")
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def _node(graph: dict, s: GuideSetting) -> dict:
        node = graph.get(s.node)
        if not isinstance(node, dict):
            raise ValueError(f"setting {s.name}: node {s.node} is not in step {s.step}")
        return node

    def _apply(self, graph: dict, s: GuideSetting, value) -> str:
        node = self._node(graph, s)
        if s.kind == "ltx_director":
            if not isinstance(value, dict) or not isinstance(value.get("prompts"), list):
                raise ValueError(f'{s.name}: give {{"prompts": [one per segment], "frames": [optional]}}')
            frames = value.get("frames")
            if frames is not None and not isinstance(frames, list):
                raise ValueError(f"{s.name}: `frames` is a list of segment lengths")
            node["inputs"] = LtxDirectorStory(node["inputs"]).write([str(p) for p in value["prompts"]], frames)
            total = node["inputs"].get("duration_frames")
            return f"  {s.name}: {len(value['prompts'])} segments rewritten" + (f", {total} frames" if frames else "")
        current = node["inputs"].get(s.input)
        if isinstance(current, list):
            raise ValueError(f"{s.name}: {s.node}.{s.input} is wired to another node — not a setting")
        node["inputs"][s.input] = _same_type(s.name, current, value)
        return f"  {s.name}: {node['inputs'][s.input]!r}"[:300]

    def _show(self, graph: dict, s: GuideSetting) -> str:
        node = self._node(graph, s)
        if s.kind == "ltx_director":
            segs = LtxDirectorStory(node["inputs"]).segments()
            return f"  {s.name} — {s.what}\n" + "\n".join(
                f"    {g['n']}. [{g['type']}, {g['frames']} frames] {g['prompt']}" for g in segs)
        return f"  {s.name} — {s.what}: {node['inputs'].get(s.input)!r}"[:600]


def _same_type(name: str, current, value):
    """`value` as the type the input already holds — a seed stays an int, a prompt a string."""
    if isinstance(current, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{name}: true or false")
        return value
    if isinstance(current, int):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError(f"{name}: a whole number")
        return int(value)
    if isinstance(current, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name}: a number")
        return float(value)
    if isinstance(current, str):
        if not isinstance(value, str):
            raise ValueError(f"{name}: text")
        return value
    raise ValueError(f"{name}: this input's value cannot be set here")


__all__ = ["TemplateSetTool"]

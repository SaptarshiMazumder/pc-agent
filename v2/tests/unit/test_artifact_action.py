"""Self-declared canvas ARTIFACT ACTIONS: a tool advertises `artifact_action` (e.g. figure_to_svg's
"Convert to Vector" on PNGs); it surfaces through the plugin catalog so a client renders the button
generically, and the tools.invoke RPC runs only opted-in tools and returns their artifacts.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.infrastructure.plugins.catalog import catalog_from_tools


class _Tool:
    plugin = "vectorize"
    name = "figure_to_svg"
    description = "convert a labelled figure PNG into an editable SVG"
    artifact_action = {"mime": ["image/png"], "label": "Convert to Vector", "param": "image"}


class _PlainTool:
    plugin = "vectorize"
    name = "trace_image"
    description = "geometric trace"
    # no artifact_action -> not a UI action


def test_catalog_surfaces_artifact_action():
    cat = catalog_from_tools(SimpleNamespace(plugins={}), [_Tool(), _PlainTool()])
    tools = {t["name"]: t for t in cat["vectorize"]["tools"]}
    assert tools["figure_to_svg"]["artifact_action"] == {
        "mime": ["image/png"],
        "label": "Convert to Vector",
        "param": "image",
    }
    assert tools["trace_image"]["artifact_action"] == {}  # default: not an action


def test_real_figure_to_svg_declares_the_action():
    # the actual tool ships the PNG -> Convert to Vector action
    for d in ("plugins/vectorize", "plugins/figures", "plugins/figure-art"):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / d))
    from figure_to_svg_tool import FigureToSvgTool

    a = FigureToSvgTool.artifact_action
    assert a["mime"] == ["image/png"] and a["param"] == "image" and a["label"]


# ---- tools.invoke gating (unit, no network: use a stub tool) ---------------------------------
class _Gate:
    """Minimal stand-in exposing the gateway's _tools_invoke over a fake service."""

    def __init__(self, tool):
        self.service = SimpleNamespace(
            find_tool=lambda n, a=None: tool if tool and tool.name == n else None
        )

    # bind the real method so we test the actual gating/return logic
    from agent_runtime.presentation.gateway import Gateway

    _tools_invoke = Gateway._tools_invoke


class _ActionTool:
    name = "act"
    artifact_action = {"mime": ["image/png"], "label": "Go", "param": "image"}

    async def execute(self, call_id, params, abort, on_update=None):
        from agent_runtime.application.interfaces.tool import ToolResult

        # echo the path back as if it produced an artifact (path need not exist -> dropped, fine)
        return ToolResult.text(f"ran on {params.get('image')}", artifacts=[])


class _NoActionTool:
    name = "plain"
    artifact_action = {}

    async def execute(self, call_id, params, abort, on_update=None):
        from agent_runtime.application.interfaces.tool import ToolResult

        return ToolResult.text("ok")


def test_tools_invoke_runs_action_tool():
    g = _Gate(_ActionTool())
    out = asyncio.run(g._tools_invoke({"name": "act", "params": {"image": "/x/y.png"}}))
    assert "ran on /x/y.png" in out["text"] and out["artifacts"] == []


def test_tools_invoke_rejects_non_action_tool():
    g = _Gate(_NoActionTool())
    with pytest.raises(RuntimeError, match="not invokable"):
        asyncio.run(g._tools_invoke({"name": "plain", "params": {}}))


def test_tools_invoke_unknown_tool():
    g = _Gate(None)
    with pytest.raises(RuntimeError, match="not available"):
        asyncio.run(g._tools_invoke({"name": "nope", "params": {}}))


class _Guarded:
    """A reliability WRAPPER (like GuardedTool): the real tool is in `_inner`; the wrapper itself
    does NOT expose `artifact_action`. find_tool returns THIS — the gate must unwrap to see it."""

    def __init__(self, inner):
        self._inner = inner
        self.name = inner.name

    async def execute(self, call_id, params, abort, on_update=None):
        return await self._inner.execute(call_id, params, abort, on_update)


def test_tools_invoke_unwraps_guarded_tool():
    # regression: the gate rejected every UI action because it checked artifact_action on the
    # wrapper instead of the inner tool.
    g = _Gate(_Guarded(_ActionTool()))
    out = asyncio.run(g._tools_invoke({"name": "act", "params": {"image": "/x/y.png"}}))
    assert "ran on /x/y.png" in out["text"]

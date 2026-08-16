"""validate_workflow — check a workflow twice: as a document, and against the real server.

TWO LAYERS, AND THE SECOND IS THE ONE THAT MATTERS.

Structural checks (ids, links, endpoints) catch the errors that make a file fail to IMPORT. They
need nothing but the JSON, so they always run.

Live checks compare the graph to `/object_info` on the server it will actually run on: does this
node class exist HERE, are its required inputs present, is that checkpoint name one of the values
this server will accept. These catch the errors that import fine and then fail at queue time —
which is most of them, because a workflow is mostly strings naming things on somebody else's
machine.

WHY IT IS CODE AND NOT A CLAIM. Anything the agent asserts about its own output is a claim;
anything this returns is a fact. That difference is what makes a generated workflow trustworthy
enough to hand over. It still is not proof that it RUNS — only `run_workflow` is that.

If the server cannot be reached, the structural result is returned WITH a line saying the live
half did not run. It never silently reports "OK" about checks it did not perform.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from comfy_client import ComfyClient, ComfyError
from object_info_cache import ObjectInfoCache


class ValidateWorkflowTool(Tool):
    name = "validate_workflow"
    label = "Validate Workflow"
    default_retryable = False
    description = (
        "Check a ComfyUI workflow JSON: its structure (format, node and link ids, dangling "
        "links, input references) AND, when the server is reachable, whether every node class it "
        "uses is installed there, its required inputs are present, and its model/sampler names "
        "are values that server accepts. Run this after writing or revising a workflow. It does "
        "not prove the graph runs — use run_workflow for that."
    )
    parameters = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "description": "Path to the workflow JSON file."}
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        raw = str(params.get("path") or "").strip()
        if not raw:
            return ToolResult.text("validate_workflow needs a `path`", is_error=True)
        path = Path(raw)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ToolResult.text(f"[x] no workflow at {path}", is_error=True)
        except json.JSONDecodeError as e:
            # Line and column, because "invalid JSON" alone means re-reading the whole file.
            return ToolResult.text(
                f"[x] invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}", is_error=True
            )
        except OSError as e:
            return ToolResult.text(f"[x] could not read {path}: {e}", is_error=True)

        if not isinstance(data, dict):
            return ToolResult.text("[x] the workflow root must be a JSON object", is_error=True)

        is_ui = isinstance(data.get("nodes"), list)
        shape = "UI export" if is_ui else "API format"
        count = len(data["nodes"]) if is_ui else len(data)
        problems = _check_ui(data) if is_ui else _check_api(data)

        # -- the live half ------------------------------------------------------------------
        live_note = ""
        try:
            client = ComfyClient.from_settings()
            catalogue = await ObjectInfoCache.all(client)
            problems += (
                _check_ui_classes(data, catalogue) if is_ui else _check_api_live(data, catalogue)
            )
            live_note = f"Checked against the live node catalogue on {client.base}."
        except (ComfyError, RuntimeError) as e:
            live_note = (
                f"NOT checked against a server — {e}\n"
                f"Structure only: this cannot tell you whether these nodes or models exist "
                f"anywhere."
            )

        if problems:
            body = "\n".join(f"[x] {p}" for p in problems)
            return ToolResult.text(
                f"{path.name}: {len(problems)} problem(s) — {shape}, {count} node(s)\n"
                f"{body}\n\n{live_note}",
                is_error=True,
            )
        return ToolResult.text(f"{path.name}: OK — {shape}, {count} node(s).\n{live_note}")


# ---------------------------------------------------------------------------------------------
# structural — needs nothing but the file
# ---------------------------------------------------------------------------------------------


def _check_ui(data: dict) -> list[str]:
    """`{"nodes": [...], "links": [...]}` — the format the ComfyUI canvas saves."""
    problems: list[str] = []
    nodes = data.get("nodes") or []
    ids: set = set()
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            problems.append(f"nodes[{i}] is not an object")
            continue
        nid = node.get("id")
        if nid is None:
            problems.append(f"nodes[{i}] has no id")
        elif nid in ids:
            problems.append(f"duplicate node id {nid!r}")
        else:
            ids.add(nid)
        if not node.get("type"):
            problems.append(f"node {nid!r} has no type (the node class name)")

    # links: [link_id, from_node, from_slot, to_node, to_slot, type]
    for i, link in enumerate(data.get("links") or []):
        if not isinstance(link, list) or len(link) < 5:
            problems.append(f"links[{i}] is not a [id, from, from_slot, to, to_slot, type] array")
            continue
        # A link to a node that is not in the file is the failure that imports "successfully"
        # and then produces an empty canvas or a broken graph.
        if link[1] not in ids:
            problems.append(f"link {link[0]!r} comes from node {link[1]!r}, which does not exist")
        if link[3] not in ids:
            problems.append(f"link {link[0]!r} goes to node {link[3]!r}, which does not exist")
    return problems


def _check_api(data: dict) -> list[str]:
    """A flat `{"3": {"class_type": …, "inputs": {…}}, …}` — the format /prompt accepts."""
    problems: list[str] = []
    for nid, node in data.items():
        if not isinstance(node, dict):
            problems.append(f"node {nid!r} is not an object")
            continue
        if not node.get("class_type"):
            problems.append(f"node {nid!r} has no class_type")
        inputs = node.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            problems.append(f"node {nid!r} has a non-object `inputs`")
            continue
        for key, value in (inputs or {}).items():
            if _is_link(value) and value[0] not in data:
                problems.append(
                    f"node {nid!r} input {key!r} reads from node {value[0]!r}, "
                    f"which is not in this workflow"
                )
    return problems


# ---------------------------------------------------------------------------------------------
# live — compared against what the server has installed
# ---------------------------------------------------------------------------------------------


def _check_ui_classes(data: dict, catalogue: dict) -> list[str]:
    """A canvas export is not runnable through the API, so only the class names are checkable —
    but a missing class is exactly what makes the user's import show a red node."""
    problems = []
    for node in data.get("nodes") or []:
        if isinstance(node, dict) and node.get("type") and node["type"] not in catalogue:
            problems.append(
                f"node {node.get('id')!r} uses {node['type']!r}, which is NOT installed on this "
                f"server (it would import as a red 'missing node')"
            )
    return problems


def _check_api_live(data: dict, catalogue: dict) -> list[str]:
    problems: list[str] = []
    has_output = False
    for nid, node in data.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        spec = catalogue.get(class_type)
        if not spec:
            problems.append(
                f"node {nid!r}: {class_type!r} is not installed on this server. "
                f"Search comfy_nodes for what is."
            )
            continue
        if spec.get("output_node"):
            has_output = True

        inputs = node.get("inputs") or {}
        required = ((spec.get("input") or {}).get("required")) or {}
        for key, decl in required.items():
            if key not in inputs:
                problems.append(
                    f"node {nid!r} ({class_type}) is missing required input {key!r} "
                    f"({_type_of(decl)})"
                )
                continue
            value = inputs[key]
            if _is_link(value):
                continue  # a connection; its type is the server's business, not ours
            choices = decl[0] if isinstance(decl, list) and isinstance(decl[0], list) else None
            if choices is not None and value not in choices:
                # THE most common runtime failure: a checkpoint or sampler name that does not
                # exist on this box. Near-misses are shown because it is usually a suffix.
                near = [c for c in choices if isinstance(c, str) and _similar(str(value), c)][:5]
                hint = f" Did you mean: {', '.join(near)}?" if near else ""
                problems.append(
                    f"node {nid!r} ({class_type}) input {key!r}={value!r} is not one of the "
                    f"{len(choices)} value(s) this server accepts.{hint}"
                )

    if data and not has_output:
        problems.append(
            "no output node in this workflow — nothing will be saved. Add a SaveImage (or "
            "another node whose output_node is true)."
        )
    return problems


def _is_link(value) -> bool:
    """A connection is `["<upstream node id>", <output index>]`; anything else is a literal."""
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)


def _type_of(decl) -> str:
    if isinstance(decl, list) and decl:
        return "one of a list of values" if isinstance(decl[0], list) else str(decl[0])
    return str(decl)


def _similar(a: str, b: str) -> bool:
    """Cheap near-miss test — shared stem, ignoring case and extension. Good enough to catch
    'sd_xl_base.safetensors' vs 'sd_xl_base_1.0.safetensors', which is the real case."""
    a, b = a.lower().rsplit(".", 1)[0], b.lower().rsplit(".", 1)[0]
    return a in b or b in a or a[:8] == b[:8]

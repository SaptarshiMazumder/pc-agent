"""Agent-authored tool (created at runtime by create_tool). Edit with care."""

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class GeneratedTool(Tool):
    name = 'validate_comfyui_workflow'
    label = 'Validate Comfyui Workflow'
    default_retryable = False
    description = 'Structurally validate a ComfyUI UI workflow JSON file after generating or revising it. Checks JSON shape, node/link IDs, endpoints, slots, and consistency between top-level links and node input/output references. This does not execute ComfyUI or prove custom-node compatibility.'
    parameters = {'type': 'object', 'required': ['path'], 'properties': {'path': {'type': 'string', 'description': 'Path to a ComfyUI UI workflow JSON file in the agent workspace.'}}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            import json
            from pathlib import Path

            raw_path = str(params.get("path", "")).strip()
            if not raw_path:
                return ToolResult.text("path is required", is_error=True)

            path = Path(raw_path)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return ToolResult.text(f"workflow not found: {path}", is_error=True)
            except json.JSONDecodeError as exc:
                return ToolResult.text(
                    f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
                    is_error=True,
                )
            except Exception as exc:
                return ToolResult.text(f"could not read workflow: {exc}", is_error=True)

            errors = []
            warnings = []
            if not isinstance(data, dict):
                return ToolResult.text("workflow root must be a JSON object", is_error=True)

            nodes = data.get("nodes")
            links = data.get("links")
            if not isinstance(nodes, list):
                errors.append("top-level 'nodes' must be an array")
                nodes = []
            if not isinstance(links, list):
                errors.append("top-level 'links' must be an array")
                links = []

            node_by_id = {}
            for index, node in enumerate(nodes):
                if not isinstance(node, dict):
                    errors.append(f"nodes[{index}] is not an object")
                    continue
                node_id = node.get("id")
                if not isinstance(node_id, int):
                    errors.append(f"nodes[{index}].id must be an integer")
                    continue
                if node_id in node_by_id:
                    errors.append(f"duplicate node id {node_id}")
                node_by_id[node_id] = node
                if not isinstance(node.get("type"), str) or not node.get("type"):
                    errors.append(f"node {node_id} has no valid type")
                if not isinstance(node.get("inputs", []), list):
                    errors.append(f"node {node_id}.inputs must be an array when present")
                if not isinstance(node.get("outputs", []), list):
                    errors.append(f"node {node_id}.outputs must be an array when present")

            link_by_id = {}
            for index, link in enumerate(links):
                if not isinstance(link, list) or len(link) < 6:
                    errors.append(f"links[{index}] must be an array with at least 6 items")
                    continue
                link_id, origin_id, origin_slot, target_id, target_slot, _link_type = link[:6]
                if not all(isinstance(v, int) for v in (link_id, origin_id, origin_slot, target_id, target_slot)):
                    errors.append(f"links[{index}] id/endpoints/slots must be integers")
                    continue
                if link_id in link_by_id:
                    errors.append(f"duplicate link id {link_id}")
                link_by_id[link_id] = link
                origin = node_by_id.get(origin_id)
                target = node_by_id.get(target_id)
                if origin is None:
                    errors.append(f"link {link_id} references missing origin node {origin_id}")
                if target is None:
                    errors.append(f"link {link_id} references missing target node {target_id}")
                if origin is not None:
                    outputs = origin.get("outputs", [])
                    if not isinstance(outputs, list) or origin_slot < 0 or origin_slot >= len(outputs):
                        errors.append(f"link {link_id} origin slot {origin_slot} is absent on node {origin_id}")
                    else:
                        listed = outputs[origin_slot].get("links") if isinstance(outputs[origin_slot], dict) else None
                        if isinstance(listed, list) and link_id not in listed:
                            errors.append(f"link {link_id} is missing from node {origin_id} output slot {origin_slot}")
                if target is not None:
                    inputs = target.get("inputs", [])
                    if not isinstance(inputs, list) or target_slot < 0 or target_slot >= len(inputs):
                        errors.append(f"link {link_id} target slot {target_slot} is absent on node {target_id}")
                    else:
                        recorded = inputs[target_slot].get("link") if isinstance(inputs[target_slot], dict) else None
                        if recorded != link_id:
                            errors.append(
                                f"node {target_id} input slot {target_slot} records link {recorded!r}, expected {link_id}"
                            )

            for node_id, node in node_by_id.items():
                for slot, inp in enumerate(node.get("inputs", []) if isinstance(node.get("inputs", []), list) else []):
                    if not isinstance(inp, dict):
                        errors.append(f"node {node_id} input slot {slot} is not an object")
                        continue
                    ref = inp.get("link")
                    if ref is not None and ref not in link_by_id:
                        errors.append(f"node {node_id} input slot {slot} references absent link {ref}")
                for slot, out in enumerate(node.get("outputs", []) if isinstance(node.get("outputs", []), list) else []):
                    if not isinstance(out, dict):
                        errors.append(f"node {node_id} output slot {slot} is not an object")
                        continue
                    refs = out.get("links")
                    if refs is not None and not isinstance(refs, list):
                        errors.append(f"node {node_id} output slot {slot}.links must be an array or null")
                    elif isinstance(refs, list):
                        for ref in refs:
                            if ref not in link_by_id:
                                errors.append(f"node {node_id} output slot {slot} references absent link {ref}")

            node_ids = list(node_by_id)
            link_ids = list(link_by_id)
            last_node_id = data.get("last_node_id")
            last_link_id = data.get("last_link_id")
            if node_ids and (not isinstance(last_node_id, int) or last_node_id < max(node_ids)):
                errors.append(f"last_node_id must be an integer >= {max(node_ids)}")
            if link_ids and (not isinstance(last_link_id, int) or last_link_id < max(link_ids)):
                errors.append(f"last_link_id must be an integer >= {max(link_ids)}")
            for key in ("groups", "config", "extra", "version"):
                if key not in data:
                    warnings.append(f"top-level '{key}' is absent")

            summary = [
                f"Structural validation: {'FAIL' if errors else 'PASS'}",
                f"File: {path}",
                f"Nodes: {len(nodes)}; links: {len(links)}; errors: {len(errors)}; warnings: {len(warnings)}",
            ]
            if errors:
                summary.append("Errors:\n- " + "\n- ".join(errors))
            if warnings:
                summary.append("Warnings:\n- " + "\n- ".join(warnings))
            summary.append(
                "Scope: JSON/graph consistency only. This does not verify node implementations, model compatibility, local availability, or execution."
            )
            return ToolResult.text("\n".join(summary), is_error=bool(errors))
        except Exception as e:  # noqa: BLE001 — never let an authored tool crash the loop
            return ToolResult.text(f"validate_comfyui_workflow failed: {type(e).__name__}: {e}", is_error=True)


def register(api, ctx):
    api.register_tool(GeneratedTool())

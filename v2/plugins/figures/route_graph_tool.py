"""layout_flowchart: compute node positions (when absent) and routed connector paths between elements.

The deterministic geometry behind flows/arrows. Give it nodes + edges; get back each node's box
and each edge's waypoint list. Two modes:
  - nodes WITHOUT x/y  -> it lays them out in layers (good for a from-scratch flow on a blank/overlay).
  - nodes WITH x/y     -> it only routes (e.g. you already know where structures sit on artwork
                          because a vision model located them) — endpoints clip to the box border.

The output `edges[].points` + `route` drop straight into render_editable_overlay's `arrow` elements, so the
agent never hand-computes elbow coordinates.
"""

from __future__ import annotations

import asyncio

from agentd.application.interfaces.tool import Tool, ToolResult
import figures_routing as routing


class RouteGraphTool(Tool):
    name = "layout_flowchart"
    description = (
        "Compute layout and/or connector routing for a node-edge graph. Input: `nodes` "
        "[{id, x?, y?, w?, h?}] and `edges` [{from, to, route?}]. If nodes lack x/y, they are placed "
        "in layers along `direction` (RIGHT|LEFT|DOWN|UP). Each edge is routed straight | orthogonal "
        "(elbow) | curved, with endpoints clipped to the node borders. Returns {nodes, edges with "
        "`points`, width, height} — feed edges[].points/route directly into render_editable_overlay arrows."
    )
    label = "Route Graph"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["nodes", "edges"],
        "properties": {
            "nodes": {
                "type": "array",
                "description": "Nodes. Each {id, w?, h?, and optional x,y top-left if already placed}.",
                "items": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string"},
                        "x": {"type": "number"}, "y": {"type": "number"},
                        "w": {"type": "number"}, "h": {"type": "number"},
                    },
                },
            },
            "edges": {
                "type": "array",
                "description": "Edges. Each {from, to, route?: straight|orthogonal|curved, label?}.",
                "items": {
                    "type": "object",
                    "required": ["from", "to"],
                    "properties": {
                        "from": {"type": "string"}, "to": {"type": "string"},
                        "route": {"type": "string", "enum": ["straight", "orthogonal", "curved"]},
                        "label": {"type": "string"},
                    },
                },
            },
            "direction": {"type": "string", "enum": ["RIGHT", "LEFT", "DOWN", "UP"],
                          "description": "Layout flow direction (only used when placing). Default RIGHT."},
            "router": {"type": "string", "enum": ["straight", "orthogonal", "curved"],
                       "description": "Default routing for edges without their own `route`. Default orthogonal."},
            "node_gap": {"type": "number", "description": "Cross-axis gap between nodes in a layer. Default 40."},
            "layer_gap": {"type": "number", "description": "Gap between layers. Default 120."},
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(
                routing.route,
                params["nodes"], params["edges"],
                direction=params.get("direction", "RIGHT"),
                router=params.get("router", "orthogonal"),
                node_gap=float(params.get("node_gap", 40)),
                layer_gap=float(params.get("layer_gap", 120)),
            )
        except Exception as e:
            return ToolResult.text(f"layout_flowchart failed: {e}", is_error=True)
        return ToolResult.text(
            f"Routed {len(r['nodes'])} node(s), {len(r['edges'])} edge(s); "
            f"canvas {r['width']}x{r['height']}. Feed edges[].points into render_editable_overlay arrows.",
            details=r)

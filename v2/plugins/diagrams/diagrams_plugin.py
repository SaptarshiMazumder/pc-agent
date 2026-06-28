"""diagrams plugin — render diagram-as-code to images.

Today one tool: `plantuml` (PlantUML -> PNG). Other engines (mermaid, graphviz, ...) would
join here as sibling tools, each named for its engine.
"""

from __future__ import annotations

from plantuml_tool import PlantumlTool


def register(api, ctx):
    api.register_tool(PlantumlTool(ctx.config))

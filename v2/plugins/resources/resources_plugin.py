"""Built-in 'resources' bundle — the resource tool: described, CRUD-able workspace resources
(list/read/create/edit/delete/describe), kept in a cached index.

Gating ported from build_tools: registers only when a Resource Manager is wired (via DI,
``ctx.resource_manager``). OFF / no manager => the tool never exists.
"""

from __future__ import annotations


def register(api, ctx):
    if ctx.resource_manager is None:
        return
    from resource_tool import ResourceTool

    api.register_tool(ResourceTool(ctx.resource_manager))

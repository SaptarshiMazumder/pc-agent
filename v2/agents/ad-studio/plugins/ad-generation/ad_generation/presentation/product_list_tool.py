"""product_list — the shop's product catalogue. Free."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.shop_product_service import ShopProductService
from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class ProductListTool(Tool):
    name = "product_list"
    label = "List products"
    plugin = PLUGIN
    description = "The shop's products (set up once, picked for any story): key, title, kind, price, photos."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, products: ShopProductService, workspace: RunWorkspace) -> None:
        self._products = products
        self._ws = workspace

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            found = self._products.all()
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"product_list: {type(e).__name__}: {e}", is_error=True)
        lines = [f"{p.key} · {p.title} · {p.kind} · {p.price or 'no price'} · {len(p.all_photos())} photos" for p in found]
        return ToolResult.text("\n".join(lines) or "no products yet", details={"root": str(self._ws.root()), "products": [p.to_dict() for p in found]})

"""product_remove — take a product out of the catalogue (its photos stay in the library). Free."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.shop_product_service import ShopProductService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class ProductRemoveTool(Tool):
    name = "product_remove"
    label = "Remove a product"
    plugin = PLUGIN
    description = "Remove a product from the catalogue: `key`. Its photos stay in the library."
    parameters = {"type": "object", "required": ["key"], "properties": {"key": {"type": "string"}}}

    def __init__(self, products: ShopProductService) -> None:
        self._products = products

    async def execute(self, tool_call_id, params, abort, on_update=None):
        key = str(params.get("key") or "")
        try:
            self._products.remove(key)
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"product_remove: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"{key} removed", details={"removed": key})

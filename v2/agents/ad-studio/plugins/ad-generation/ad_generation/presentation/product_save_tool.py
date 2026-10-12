"""product_save — add a product to the shop's catalogue, or change one. Free."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.shop_product_service import ShopProductService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class ProductSaveTool(Tool):
    name = "product_save"
    label = "Save a product"
    plugin = PLUGIN
    description = (
        "Add a product (no `key`: title, kind wear|jewellery|other, photos — workspace paths, main first — "
        "and optionally angles, in_use, description, price), or change one (its `key` and the fields). "
        "Only what the user gave; never invent a price. Free."
    )
    parameters = {
        "type": "object",
        "properties": {
            "key": {"type": "string"}, "title": {"type": "string"},
            "kind": {"type": "string", "enum": ["wear", "jewellery", "other"]},
            "description": {"type": "string"}, "price": {"type": "string"},
            "photos": {"type": "array", "items": {"type": "string"}},
            "angles": {"type": "array", "items": {"type": "string"}},
            "in_use": {"type": "array", "items": {"type": "string"}},
        },
    }

    def __init__(self, products: ShopProductService) -> None:
        self._products = products

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            product = self._products.save(dict(params))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"product_save: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"product {product.key}: {product.title}", details={"product": product.to_dict()})

"""The panels of a product sheet, cut apart by the sheet's fixed layout (product_sheet_layout)."""

from __future__ import annotations

from typing import Protocol


class SheetPanels(Protocol):
    def panels(self, sheet: str) -> list[str]:
        """The sheet's panels as images saved beside it (<stem>-panel-<n>.png), in the layout's
        VIEWS order; their workspace paths."""
        ...

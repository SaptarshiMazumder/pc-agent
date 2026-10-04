"""SheetPanels with Pillow: the grid cut by position, each cell trimmed by a small inset so the gaps
between panels stay out of the crop; made once per sheet (redone if the sheet is newer)."""

from __future__ import annotations

from PIL import Image

from ad_generation.domain.product_sheet_layout import COLS, ROWS, VIEWS
from ad_generation.infrastructure.run_workspace import RunWorkspace

_INSET = 0.03  # of a cell's size, on every side


class PillowSheetPanels:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def panels(self, sheet: str) -> list[str]:
        stem = sheet.rsplit(".", 1)[0]
        rels = [f"{stem}-panel-{n}.png" for n in range(1, len(VIEWS) + 1)]
        src = self._ws.path(sheet)
        made = [self._ws.path(r) for r in rels]
        if all(p.is_file() and p.stat().st_mtime >= src.stat().st_mtime for p in made):
            return rels
        with Image.open(src) as im:
            im = im.convert("RGB")
            cw, ch = im.width / COLS, im.height / ROWS
            for n, dest in enumerate(made):
                col, row = n % COLS, n // COLS
                box = (
                    int(col * cw + cw * _INSET),
                    int(row * ch + ch * _INSET),
                    int((col + 1) * cw - cw * _INSET),
                    int((row + 1) * ch - ch * _INSET),
                )
                im.crop(box).save(dest, "PNG")
        return rels

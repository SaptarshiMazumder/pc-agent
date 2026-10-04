"""The face close-up cut from a cast member's character sheet.

A whole sheet is several panels (body views, face close-ups), and image models copy what a
reference SHOWS — a sheet as the identity reference drew collages and lost the face in the grid.
The close-up alone gives the face, the hair and the skin at full size, and no layout to copy.
"""

from __future__ import annotations

from typing import Protocol


class FaceCrops(Protocol):
    def face(self, sheet: str) -> str:
        """The face close-up of the sheet at `sheet`, saved beside it (<stem>-face.png); its
        workspace path. Raises when the sheet shows no face."""
        ...

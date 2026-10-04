"""The product sheet's fixed layout: a 3×2 grid of the product alone, one view per panel.

Fixed so the sheet the image model draws can be cut back into its panels by position — each
panel then goes to the models as its OWN reference ("the product from the back"), because a whole
grid used as a reference is what makes a model draw a collage.
"""

from __future__ import annotations

COLS = 3
ROWS = 2
# Left to right, top row then bottom row — the order the prompt asks for and the cutter reads.
VIEWS = ("front", "back", "left side", "right side", "top-down", "close-up of the label or finest detail")
ASPECT = "3:2"  # three square panels across, two down

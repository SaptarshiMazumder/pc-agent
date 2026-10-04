"""What a cast member LOOKS like, out of the description written for their character sheet.

That description is a sheet-making brief: features, but also how to draw the sheet ("every view",
"the reference image"), consistency rules ("never alter her face"), a default outfit and jewellery
("a thin black choker", "oval gold earrings"), makeup and glow ("luminous", "bronzed"). In an ad's
image prompt all of that fights the ad — the sheet wording draws panels, the outfit and jewellery
compete with the product, the glow reads as plastic. Only the features are kept: face, eyes,
skin, hair colour and texture, build, age.
"""

from __future__ import annotations

import re

# A sentence that is an instruction about the sheet or the rules, not a feature.
_INSTRUCTION = re.compile(r"\b(views?|sheet|real or famous|consisten\w*|never|generate|create|crop\w*)\b", re.I)
# "Preserve her face exactly: fair skin…" — a lead-in whose features come after the colon.
_FACE_LEAD = re.compile(r"\b(preserve|keep|face|features?)\b", re.I)
# A lead-in whose whole sentence is not about her: "Default outfit: …", "Styling: …".
_OTHER_LEAD = re.compile(r"\b(outfit|styling|style|default|wardrobe|clothes|consistency)\b", re.I)
# One clause that is not about her face, hair or body.
_NOT_FEATURE = re.compile(
    r"\b(makeup|make-up|eyeliner|eyeshadow|lipstick|luminous|dewy|glow\w*|glossy|shiny|bronzed|editorial|"
    r"beauty|glamou?r\w*|earrings?|necklace|choker|jewel\w*|bindi|presence|fashion|look|styling|style|"
    r"signature|proportions|outfit|top|t-shirt|shirt|tank|leggings|jeans|dress|sleeve\w*|feet|barefoot|"
    r"wear\w*|clothes|same|identity|recogni\w*|reference|supplied|provided|attached|model)\b",
    re.I,
)
# Words that only say she is made up.
_MADE_UP = re.compile(r"\b(AI[- ]generated|AI|fictional|recurring|original)\b\s*", re.I)
# An order ("Use natural…"), not a description.
_ORDER = re.compile(r"(use|keep|preserve|maintain|make)\b", re.I)


def looks_from_description(description: str) -> str:
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.;])\s+", description):
        head, sep, tail = sentence.partition(":")
        if sep and _OTHER_LEAD.search(head):
            continue
        body = tail if sep and _FACE_LEAD.search(head) else sentence
        if _INSTRUCTION.search(body):
            continue
        clauses = [c.strip() for c in re.split(r",\s*|\s+and\s+(?=an?\s)", body.strip().rstrip(".;")) if c.strip()]
        clauses = [_MADE_UP.sub("", c).strip() for c in clauses if not _NOT_FEATURE.search(c)]
        clauses = [re.sub(r"^(and|with)\s+", "", c) for c in clauses if c]
        clauses = [re.sub(r"^A\s+(?=[AEIOU])", "An ", c) for c in clauses if not _ORDER.match(c)]
        # A bare checklist from a consistency rule ("eyes, brows, nose, lips") says nothing.
        if len(clauses) >= 3 and all(len(c.split()) <= 2 for c in clauses):
            continue
        if clauses:
            kept.append(", ".join(clauses) + ".")
    return " ".join(kept)

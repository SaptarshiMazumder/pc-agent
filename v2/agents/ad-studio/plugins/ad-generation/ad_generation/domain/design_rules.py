"""What a slide design (HTML/CSS) may contain — checked before it is rendered or kept.

No scripts or embedded pages; nothing loaded but the pictures and clips it was given and fonts
from Google Fonts; a design for a clip has exactly one slot for it, a design for a picture none.
"""

from __future__ import annotations

import re

_FONT_HOSTS = ("https://fonts.googleapis.com/", "https://fonts.gstatic.com/")
_FORBIDDEN = (("<script", "no <script> — motion is CSS only"), ("<iframe", "no <iframe>"), ("<form", "no forms"),
              ("javascript:", "no javascript: links"), ("<object", "no <object>"), ("<embed", "no <embed>"))
_REFS = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']|url\(\s*["']?([^"')]+)["']?\s*\)""", re.I)


class DesignRules:
    @staticmethod
    def problems(html: str, allowed: set[str], clip: str = "") -> list[str]:
        """Everything wrong with `html`, each said so the designer can fix it; [] when it may be rendered."""
        out = []
        low = html.lower()
        if "<html" not in low or "<body" not in low:
            out.append("the design must be one complete HTML document (<html>, <head>, <body>)")
        out += [why for tag, why in _FORBIDDEN if tag in low]
        for m in _REFS.finditer(html):
            ref = (m.group(1) or m.group(2) or "").strip()
            if not ref or ref.startswith(("#", "data:")) or ref.startswith(_FONT_HOSTS) or ref in allowed:
                continue
            out.append(f"'{ref}' is not one of the given pictures or clips, or Google Fonts — use only those paths, exactly")
        slots = re.findall(r"""data-video\s*=\s*["']([^"']+)["']""", html, re.I)
        if clip:
            if len(slots) != 1 or slots[0] != clip:
                out.append(f"a design for a clip has exactly ONE element with data-video=\"{clip}\" — the slot it plays in")
        elif slots:
            out.append("this slide shows a picture, not a clip — no data-video slot")
        return out

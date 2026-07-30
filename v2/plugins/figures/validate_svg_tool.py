"""validate_svg: parse an SVG and report whether it's well-formed + an element inventory.

The structural-correctness signal for the figure loop. After the agent (or render_editable_overlay) emits
SVG, this confirms it actually parses and tells the agent WHAT is in it — how many editable <text>
labels, how many arrows/paths, whether the artwork <image> is embedded, which gradients/markers/
filters are defined — so the agent can catch "I asked for 8 labels but only 6 rendered" before
shipping. Pure lxml; no browser.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from figures_common import resolve_path, svg_size


def _localname(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else str(tag)


class ValidateSvgTool(Tool):
    name = "validate_svg"
    description = (
        "Parse an SVG (string `svg` or `svg_path`) and report whether it is well-formed plus a full "
        "element inventory: counts of <text> (editable labels), <path>/arrows, <image> (embedded "
        "artwork), <rect>, <circle>, and the ids of gradients/markers/filters defined. Use it to "
        "VERIFY a figure's structure before export (e.g. confirm every label and arrow is present "
        "and editable). Returns is_error=true if the SVG is malformed, with the parse error."
    )
    label = "Validate SVG"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "properties": {
            "svg": {"type": "string", "description": "Raw SVG markup. Provide this OR svg_path."},
            "svg_path": {"type": "string", "description": "Path to a .svg file (absolute or workspace-relative)."},
        },
    }

    def __init__(self, config):
        self.config = config

    def _run(self, params: dict) -> dict:
        if bool(params.get("svg")) == bool(params.get("svg_path")):
            raise ValueError("provide exactly ONE of: svg, svg_path")
        if params.get("svg_path"):
            text = resolve_path(self.config, params["svg_path"]).read_text(encoding="utf-8")
        else:
            text = params["svg"]

        from lxml import etree

        try:
            root = etree.fromstring(text.encode("utf-8"))
        except etree.XMLSyntaxError as e:
            return {"well_formed": False, "error": str(e)}

        counts = Counter()
        ids = {"gradient": [], "marker": [], "filter": []}
        n_text_nonempty = 0
        for el in root.iter():
            tag = _localname(el.tag)
            counts[tag] += 1
            if tag == "text" and (el.text or "").strip():
                n_text_nonempty += 1
            if tag in ("linearGradient", "radialGradient") and el.get("id"):
                ids["gradient"].append(el.get("id"))
            elif tag == "marker" and el.get("id"):
                ids["marker"].append(el.get("id"))
            elif tag == "filter" and el.get("id"):
                ids["filter"].append(el.get("id"))

        w, h = svg_size(text)
        return {
            "well_formed": True,
            "width": w, "height": h,
            "has_viewbox": "viewBox" in root.attrib,
            "labels_text": counts.get("text", 0),
            "labels_nonempty": n_text_nonempty,
            "paths": counts.get("path", 0),
            "images_embedded": counts.get("image", 0),
            "rects": counts.get("rect", 0),
            "circles": counts.get("circle", 0),
            "markers": len(ids["marker"]),
            "gradients": len(ids["gradient"]),
            "filters": len(ids["filter"]),
            "element_counts": dict(counts),
            "def_ids": ids,
        }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"validate_svg failed: {e}", is_error=True)
        if not r.get("well_formed"):
            return ToolResult.text(f"SVG is NOT well-formed: {r.get('error')}", details=r, is_error=True)
        msg = (f"SVG OK — {r['width']}x{r['height']}px"
               f"{'' if r['has_viewbox'] else ' (no viewBox!)'}. "
               f"{r['labels_nonempty']} text label(s), {r['paths']} path(s), "
               f"{r['images_embedded']} embedded image(s), {r['markers']} marker(s), "
               f"{r['gradients']} gradient(s), {r['filters']} filter(s).")
        return ToolResult.text(msg, details=r)

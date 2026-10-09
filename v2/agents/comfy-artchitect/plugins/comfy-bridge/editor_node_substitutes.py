"""EditorNodeSubstitutes — built-in stand-ins for custom nodes Comfy Cloud does not have.

A workflow someone shares is often blocked by ONE node from a pack Comfy Cloud does not install —
a resize helper in an 81-node LTX graph. Refusing the whole workflow over it was the wrong answer:
when the node only does what built-in nodes do, it is rewritten into them in the EDITOR graph,
wired the same, before the machine converts it. Every swap is said in one line, so the person
knows their workflow was changed and how.

ONLY EXACT STAND-INS, one function per class: a swap must give the same result. A node used in a
way its stand-in cannot do (a mask output in use, a setting driven by a link) is NOT swapped — it
stays missing, and is named as such. Nodes inside subgraphs are not rewritten.

Pure: an editor graph in, a rewritten copy out.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable


class _CannotSwap(Exception):
    pass


class EditorNodeSubstitutes:
    def apply(self, ui: dict, missing: list[str]) -> tuple[dict, list[str], list[str]]:
        """(the rewritten copy, one line per swap made, the classes still missing)."""
        graph = copy.deepcopy(ui)
        swapped: list[str] = []
        left: set[str] = set()
        for node in list(graph.get("nodes") or []):
            cls = str(node.get("type") or "")
            if cls not in missing:
                continue
            swap = _SWAPS.get(cls)
            if swap is None:
                left.add(cls)
                continue
            try:
                swapped.append(swap(graph, node))
            except _CannotSwap as e:
                left.add(f"{cls} (node {node.get('id')}: {e})")
        # A missing class with no top-level node (inside a subgraph) is still missing.
        seen = {str(n.get("type") or "") for n in ui.get("nodes") or []}
        left |= {c for c in missing if c not in seen}
        return graph, swapped, sorted(left)


# ---------------------------------------------------------------------------- the editor graph

def _link(graph: dict, link_id) -> list | None:
    return next((lk for lk in graph.get("links") or [] if lk[0] == link_id), None)


def _new_ids(graph: dict) -> tuple[int, int]:
    """The next free node id and link id, claimed."""
    nid = int(graph.get("last_node_id") or max((n["id"] for n in graph["nodes"]), default=0)) + 1
    lid = int(graph.get("last_link_id") or max((lk[0] for lk in graph.get("links") or []), default=0)) + 1
    graph["last_node_id"], graph["last_link_id"] = nid, lid
    return nid, lid


def _input(node: dict, name: str) -> dict:
    return next((i for i in node.get("inputs") or [] if i.get("name") == name), {})


def _output_links(node: dict, slot: int) -> list:
    outs = node.get("outputs") or []
    return list((outs[slot].get("links") or []) if slot < len(outs) else [])


# ---------------------------------------------------------------------------- the stand-ins

def _image_scale_pixel_v2(graph: dict, node: dict) -> str:
    """image_scale_pixel_v2 (a resize-by-pixel-count helper): widgets [megapixels, alignment],
    outputs image / mask / width / height. Built in: ImageScaleToTotalPixels at the same
    megapixels, rounded to the same multiple (`resolution_steps`), then GetImageSize for the size."""
    if _output_links(node, 1) or _input(node, "masks").get("link") is not None:
        raise _CannotSwap("its mask is used")
    if any(_input(node, w).get("link") is not None for w in ("TotalPixels", "alignment")):
        raise _CannotSwap("its size is set by a link")
    values = list(node.get("widgets_values") or [])
    megapixels = float(values[0]) if values and isinstance(values[0], (int, float)) else 1.0
    m = re.match(r"\s*(\d+)", str(values[1])) if len(values) > 1 else None
    steps = int(m.group(1)) if m else 1
    image_in = _input(node, "images").get("link")
    image_out, width_out, height_out = _output_links(node, 0), _output_links(node, 2), _output_links(node, 3)

    node.update({
        "type": "ImageScaleToTotalPixels",
        "title": f"Scale to {megapixels:g} MP (multiples of {steps}) — was image_scale_pixel_v2",
        "inputs": [{"name": "image", "type": "IMAGE", "link": image_in}],
        "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": image_out}],
        "properties": {"Node name for S&R": "ImageScaleToTotalPixels"},
        "widgets_values": ["lanczos", megapixels, steps],
    })
    if width_out or height_out:
        size_id, link_id = _new_ids(graph)
        x, y = (node.get("pos") or [0, 0])[:2]
        graph["nodes"].append({
            "id": size_id, "type": "GetImageSize", "pos": [x + 330, y], "size": [210, 90], "flags": {},
            "order": node.get("order", 0), "mode": node.get("mode", 0),
            "inputs": [{"name": "image", "type": "IMAGE", "link": link_id}],
            "outputs": [{"name": "width", "type": "INT", "links": width_out},
                        {"name": "height", "type": "INT", "links": height_out},
                        {"name": "batch_size", "type": "INT", "links": []}],
            "properties": {"Node name for S&R": "GetImageSize"}, "widgets_values": [],
        })
        node["outputs"][0]["links"] = [*image_out, link_id]
        graph["links"].append([link_id, node["id"], 0, size_id, 0, "IMAGE"])
        for lid, slot in [(w, 0) for w in width_out] + [(h, 1) for h in height_out]:
            lk = _link(graph, lid)
            if lk is not None:
                lk[1], lk[2] = size_id, slot
    return (f"image_scale_pixel_v2 (node {node['id']}) → built-in ImageScaleToTotalPixels at {megapixels:g} MP, "
            f"multiples of {steps}" + (" + GetImageSize for its width/height" if width_out or height_out else ""))


_SWAPS: dict[str, Callable[[dict, dict], str]] = {
    "image_scale_pixel_v2": _image_scale_pixel_v2,
}


__all__ = ["EditorNodeSubstitutes"]

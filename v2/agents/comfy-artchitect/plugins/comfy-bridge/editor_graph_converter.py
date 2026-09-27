"""Turn an EDITOR-format workflow (what ComfyUI's editor saves, and what most tutorials ship) into
the API format a run needs — by asking the machine's own ComfyUI (POST /api/workflow/convert).

ONLY THE MACHINE CAN DO IT RIGHT. An editor graph stores each node's settings as a bare list of
widget values; which value is which input is known only to the node's own definition. So the
conversion is honest only on a ComfyUI that has every node the graph uses: converted without its
custom node pack, a node comes out with no class and inputs named UNKNOWN, UNKNOWN_1… — exactly
the broken "API" file a user can end up with. Hence `missing_classes` first: install those packs,
then convert. Nothing here converts by hand.

A WIRE INTO AN INPUT THE NODE NO LONGER HAS IS DROPPED, as ComfyUI's own editor drops it when it
opens an older workflow: node packs change after a tutorial is recorded, and a link to a removed
input is the one difference the machine's converter keeps. Only links are dropped, never a
setting, and each one is named so it can be said.
"""

from __future__ import annotations

#: Editor-only node types that never reach the API graph (notes, reroutes, primitives).
_EDITOR_ONLY = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode", "PrimitiveString",
                "PrimitiveInt", "PrimitiveFloat", "PrimitiveBoolean"}


class EditorGraphConverter:
    def __init__(self, *, get, post) -> None:
        self._get = get
        self._post = post

    @staticmethod
    def node_types(ui: dict) -> set[str]:
        """Every node type the editor graph uses, subgraphs included, notes and reroutes not."""
        definitions = (ui.get("definitions") or {}).get("subgraphs") or []
        subgraph_ids = {str(d.get("id")) for d in definitions if isinstance(d, dict)}
        nodes = list(ui.get("nodes") or [])
        for d in definitions:
            if isinstance(d, dict):
                nodes.extend(d.get("nodes") or [])
        return {str(n.get("type")) for n in nodes
                if isinstance(n, dict) and n.get("type") and str(n.get("type")) not in subgraph_ids
                and str(n.get("type")) not in _EDITOR_ONLY}

    def _object_info(self) -> dict:
        res = self._get("/api/object_info", timeout_s=60.0)
        if not res.ok:
            raise ValueError(f"could not read this ComfyUI's nodes (HTTP {res.status or res.error})")
        return res.json() or {}

    def missing_classes(self, ui: dict) -> list[str]:
        """Node types this machine does not have — the node packs to install before converting."""
        return sorted(self.node_types(ui) - set(self._object_info().keys()))

    def convert(self, ui: dict) -> tuple[dict, list[str]]:
        """(the API graph, the stale links dropped from it — "node 16 (Class).input")."""
        api = self._convert(ui)
        info = self._object_info()
        dropped = []
        for nid, node in api.items():
            spec = info.get(node.get("class_type"))
            if not isinstance(spec, dict):
                continue  # a node this machine does not know is not judged here
            declared = {name for section in ("required", "optional", "hidden")
                        for name in ((spec.get("input") or {}).get(section) or {})}
            inputs = node.get("inputs") or {}
            for name in [k for k, v in inputs.items() if k not in declared and isinstance(v, list)]:
                del inputs[name]
                dropped.append(f"node {nid} ({node.get('class_type')}).{name}")
        return api, dropped

    def _convert(self, ui: dict) -> dict:
        res = self._post("/api/workflow/convert", ui, timeout_s=60.0)
        if res.status == 404:
            raise ValueError("this ComfyUI is too old to convert editor workflows (no /workflow/convert) "
                             "— update ComfyUI, or export the workflow as API from its editor")
        if not res.ok:
            raise ValueError(f"ComfyUI could not convert the workflow (HTTP {res.status or res.error}): "
                             f"{(res.text or '')[:200]}")
        api = res.json()
        broken = [nid for nid, node in (api.items() if isinstance(api, dict) else [])
                  if not isinstance(node, dict) or not node.get("class_type")
                  or any(str(k).startswith("UNKNOWN") for k in (node.get("inputs") or {}))]
        if not isinstance(api, dict) or not api or broken:
            raise ValueError("the conversion came back incomplete (nodes " + ", ".join(broken[:8])
                             + ") — a node pack the workflow uses is missing on this ComfyUI")
        return api

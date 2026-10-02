"""GraphEditOps — change a stage's graph one operation at a time, never by rewriting it whole.

Re-emitting a whole graph to change one value is how a video graph lost its save node and came back
"Prompt has no outputs". An edit is instead a short list of operations:

    {"op": "set_input",   "node": "9", "input": "length", "value": 121}
    {"op": "link",        "node": "12", "input": "samples", "from": "11", "output": 0}
    {"op": "unlink",      "node": "12", "input": "vae"}
    {"op": "add_node",    "id": "30", "class_type": "LoraLoaderModelOnly", "inputs": {...}}
    {"op": "remove_node", "node": "30"}

Each operation is checked as it is applied — the node exists, the class exists on the node list,
the input is one the node takes, a link points at an output the source has, a removed node is not
still read by another — and the first one that fails stops the edit, naming why. Nothing is
written unless every operation applied.
"""

from __future__ import annotations

import copy

from api_graph import ApiGraph


class GraphEditError(ValueError):
    """An operation that cannot apply; the message says which and why."""


class GraphEditOps:
    def __init__(self, catalogue: dict) -> None:
        """:param catalogue: the node list (`/api/object_info` shape) the classes are checked against."""
        self._catalogue = catalogue or {}

    def apply(self, graph: dict, ops: list[dict]) -> tuple[dict, list[str]]:
        """(the edited copy, one line per applied op). Raises GraphEditError at the first bad op."""
        g = copy.deepcopy(graph)
        done = []
        for i, op in enumerate(ops or []):
            kind = str((op or {}).get("op") or "")
            handler = getattr(self, f"_{kind}", None) if kind in _OPS else None
            if handler is None:
                raise GraphEditError(f"op {i + 1}: unknown op '{kind}' (ops: {', '.join(_OPS)})")
            try:
                done.append(handler(g, op))
            except GraphEditError as e:
                raise GraphEditError(f"op {i + 1} ({kind}): {e}") from None
        return g, done

    # ------------------------------------------------------------------ ops

    def _set_input(self, g: dict, op: dict) -> str:
        nid, name = self._node(g, op), str(op.get("input") or "")
        self._input_known(g[nid]["class_type"], name)
        g[nid]["inputs"][name] = op.get("value")
        return f"node {nid}.{name} = {op.get('value')!r}"

    def _link(self, g: dict, op: dict) -> str:
        nid, name = self._node(g, op), str(op.get("input") or "")
        src = str(op.get("from") or "")
        if src not in g:
            raise GraphEditError(f"source node {src} is not in the graph")
        slot = op.get("output", 0)
        if type(slot) is not int or slot < 0:
            raise GraphEditError("output must be a non-negative integer")
        outs = (self._catalogue.get(g[src]["class_type"]) or {}).get("output") or []
        if outs and slot >= len(outs):
            raise GraphEditError(f"node {src} ({g[src]['class_type']}) has no output {slot}")
        self._input_known(g[nid]["class_type"], name)
        g[nid]["inputs"][name] = [src, slot]
        return f"node {nid}.{name} <- node {src} output {slot}"

    def _unlink(self, g: dict, op: dict) -> str:
        nid, name = self._node(g, op), str(op.get("input") or "")
        if name not in g[nid]["inputs"]:
            raise GraphEditError(f"node {nid} has no input '{name}'")
        del g[nid]["inputs"][name]
        return f"node {nid}.{name} removed"

    def _add_node(self, g: dict, op: dict) -> str:
        nid, cls = str(op.get("id") or "").strip(), str(op.get("class_type") or "").strip()
        if not nid or not cls:
            raise GraphEditError("add_node needs id and class_type")
        if nid in g:
            raise GraphEditError(f"node {nid} already exists")
        if self._catalogue and cls not in self._catalogue:
            raise GraphEditError(f"class '{cls}' is not on the node list (a wrong name, or a pack to install)")
        inputs = dict(op.get("inputs") or {})
        for name in inputs:
            self._input_known(cls, name)
        g[nid] = {"class_type": cls, "inputs": inputs}
        if op.get("title"):
            g[nid]["_meta"] = {"title": str(op["title"])}
        return f"node {nid} ({cls}) added"

    def _remove_node(self, g: dict, op: dict) -> str:
        nid = self._node(g, op)
        readers = [f"{c}.{n}" for c, n in ApiGraph(g).consumers(nid)]
        if readers:
            raise GraphEditError(f"node {nid} is still read by {', '.join(readers)} — relink or remove those first")
        del g[nid]
        return f"node {nid} removed"

    # ------------------------------------------------------------------ checks

    @staticmethod
    def _node(g: dict, op: dict) -> str:
        nid = str(op.get("node") or "")
        if nid not in g:
            raise GraphEditError(f"node {nid or '?'} is not in the graph")
        return nid

    def _input_known(self, cls: str, name: str) -> None:
        spec = self._catalogue.get(cls)
        if not spec or "." in name:
            return  # unknown class (reported by validation) or a dotted dynamic key
        sections = spec.get("input") or {}
        names = set((sections.get("required") or {})) | set((sections.get("optional") or {}))
        if name not in names:
            raise GraphEditError(f"{cls} has no input '{name}' (it takes: {', '.join(sorted(names))})")


_OPS = ("set_input", "link", "unlink", "add_node", "remove_node")

__all__ = ["GraphEditError", "GraphEditOps"]

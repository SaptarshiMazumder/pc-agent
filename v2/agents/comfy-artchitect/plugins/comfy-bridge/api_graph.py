"""ApiGraph — read-only questions about one API-format workflow.

The shape is ComfyUI's own: `{"<id>": {"class_type": "...", "inputs": {name: literal | [id, slot]}}}`.
Every validator asks the same few things of it — which nodes are of a class, what an input holds,
where a link comes from, what a value resolves to through a primitive — and answering them in one
place keeps the validators to their rules. Pure: no I/O.

A CLASS PATTERN is one class name or several joined by `|` ("KSampler|KSamplerAdvanced"), the
form the knowledge base writes them in.
"""

from __future__ import annotations

#: Nodes that only carry a value to another node's input. Followed when resolving a literal.
_VALUE_CARRIERS = frozenset({
    "PrimitiveInt", "PrimitiveFloat", "PrimitiveString", "PrimitiveStringMultiline", "PrimitiveBoolean",
})
_VALUE_INPUTS = ("value", "text", "string", "int", "float", "boolean")


class ApiGraph:
    def __init__(self, graph: dict) -> None:
        self.nodes: dict[str, dict] = {
            str(nid): node for nid, node in (graph or {}).items() if isinstance(node, dict)
        }

    @staticmethod
    def classes(pattern: str) -> set[str]:
        return {c.strip() for c in str(pattern or "").split("|") if c.strip()}

    @staticmethod
    def is_link(value) -> bool:
        return (isinstance(value, list) and len(value) == 2
                and isinstance(value[0], str) and isinstance(value[1], int))

    def class_of(self, nid: str) -> str:
        return str((self.nodes.get(str(nid)) or {}).get("class_type") or "")

    def inputs(self, nid: str) -> dict:
        return (self.nodes.get(str(nid)) or {}).get("inputs") or {}

    def of_class(self, pattern: str) -> list[str]:
        """Ids of every node whose class is in the pattern, in graph order."""
        wanted = self.classes(pattern)
        return [nid for nid, node in self.nodes.items() if node.get("class_type") in wanted]

    def value(self, nid: str, name: str):
        return self.inputs(nid).get(name)

    def source(self, nid: str, name: str) -> tuple[str, int] | None:
        """(upstream id, output slot) when the input is a link to a node in this graph."""
        v = self.value(nid, name)
        if self.is_link(v) and v[0] in self.nodes:
            return v[0], v[1]
        return None

    def literal(self, nid: str, name: str):
        """The input's value, followed through value-carrying primitives; None when it is wired
        to something that computes it (a GetImageSize, a math node) and so is not known here."""
        v = self.value(nid, name)
        for _ in range(4):
            if not self.is_link(v):
                return v
            up = v[0]
            if self.class_of(up) not in _VALUE_CARRIERS:
                return None
            ins = self.inputs(up)
            v = next((ins[k] for k in _VALUE_INPUTS if k in ins), None)
        return None if self.is_link(v) else v

    def consumers(self, nid: str, slot: int | None = None) -> list[tuple[str, str]]:
        """(node id, input name) of every input linked to this node (to one output slot if given)."""
        out = []
        for cid, node in self.nodes.items():
            for name, v in (node.get("inputs") or {}).items():
                if self.is_link(v) and v[0] == str(nid) and (slot is None or v[1] == slot):
                    out.append((cid, name))
        return out

    def upstream_chain(self, nid: str, via: str, limit: int = 32) -> list[str]:
        """Nodes reached by following input `via` upstream from `nid` (not including `nid`)."""
        chain, seen, cur = [], {str(nid)}, str(nid)
        for _ in range(limit):
            src = self.source(cur, via)
            if src is None or src[0] in seen:
                break
            cur = src[0]
            seen.add(cur)
            chain.append(cur)
        return chain

    def ancestors(self, nid: str, limit: int = 256) -> set[str]:
        """Every node this one depends on, through any input."""
        out, stack = set(), [str(nid)]
        while stack and len(out) < limit:
            cur = stack.pop()
            for v in self.inputs(cur).values():
                if self.is_link(v) and v[0] in self.nodes and v[0] not in out:
                    out.add(v[0])
                    stack.append(v[0])
        return out

    def strings(self) -> set[str]:
        """Every literal string input value in the graph (file names live here)."""
        return {v for node in self.nodes.values() for v in (node.get("inputs") or {}).values()
                if isinstance(v, str)}


__all__ = ["ApiGraph"]

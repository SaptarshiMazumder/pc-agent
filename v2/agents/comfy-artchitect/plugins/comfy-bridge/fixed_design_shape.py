"""The STRUCTURE of a design someone else fixed — a template's step or a Library workflow.

A served design keeps its nodes and wiring; only input VALUES change (prompt, image, size,
length, seed, a dropdown the machine does not offer). The agent once re-wrote a Library
workflow from memory to swap its first frame and its prompt, dropped the sampler, the guider,
the sigma shift and the audio decode on the way, and rendered five seconds of grey. So the
shape is recorded when the design is brought in, and every re-emit is compared with it.

Shape = per node id: its class, and every input that is a LINK. Literals are not part of it.
"""

from __future__ import annotations

from workflow_link import WorkflowLink


class FixedDesignShape:
    def __init__(self, nodes: dict[str, dict]):
        # {node_id: {"class": str, "links": {field: [upstream_id, slot]}}}
        self.nodes = nodes

    @classmethod
    def of(cls, api: dict) -> "FixedDesignShape":
        nodes: dict[str, dict] = {}
        for nid, entry in api.items():
            if not isinstance(entry, dict):
                continue
            links = {}
            for field, value in (entry.get("inputs") or {}).items():
                link = WorkflowLink.from_input(value, api, normalize_node_id=True)
                if link is not None:
                    links[field] = link.as_input()
            nodes[str(nid)] = {"class": str(entry.get("class_type") or ""), "links": links}
        return cls(nodes)

    @classmethod
    def from_json(cls, data: dict) -> "FixedDesignShape":
        return cls(dict(data or {}))

    def to_json(self) -> dict:
        return self.nodes

    def differences(self, api: dict) -> list[str]:
        """What `api` changed in this structure — empty when only input values differ."""
        new = FixedDesignShape.of(api).nodes
        out: list[str] = []
        for nid, was in self.nodes.items():
            now = new.get(nid)
            if now is None:
                out.append(f"dropped node {nid} ({was['class']})")
                continue
            if now["class"] != was["class"]:
                out.append(f"node {nid} changed class {was['class']} -> {now['class']}")
            for field in sorted(set(was["links"]) | set(now["links"])):
                a, b = was["links"].get(field), now["links"].get(field)
                if a != b:
                    out.append(f"node {nid}.{field}: link {a or 'none'} -> {b or 'none'}")
        for nid, now in new.items():
            if nid not in self.nodes:
                out.append(f"added node {nid} ({now['class']})")
        return out

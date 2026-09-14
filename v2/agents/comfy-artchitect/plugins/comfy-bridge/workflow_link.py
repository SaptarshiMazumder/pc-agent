"""The shared, pure contract for ComfyUI API input connections."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowLink:
    node_id: str
    output_slot: int

    @classmethod
    def from_input(
        cls, value: object, node_ids: Collection[str], *, normalize_node_id: bool = False,
    ) -> WorkflowLink | None:
        """Lists are connections; literal arrays use ComfyUI's __value__ wrapper.

        Only the writer may normalize integer IDs, and must serialize the returned link.
        Validation uses strict mode so it checks exactly what the GPU will receive.
        """
        if not isinstance(value, list):
            return None
        if value and all(isinstance(item, list) for item in value):
            raise ValueError(
                "is a list of links. An input takes ONE link [upstream_id, slot]. "
                "To feed several images, batch them first with ImageBatch and link its output here."
            )
        if len(value) != 2:
            raise ValueError("must be a link [upstream_id, output_slot] with exactly two entries")
        node_id, slot = value
        # bool is an int subclass, but is never a node ID or an output index.
        if normalize_node_id and type(node_id) is int:
            node_id = str(node_id)
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError(
                'needs a non-empty text node ID (e.g. ["9", 0], not [9, 0]); re-emit the workflow'
            )
        if type(slot) is not int or slot < 0:
            raise ValueError("needs a non-negative integer output slot")
        if node_id not in node_ids:
            raise ValueError(f"links to node {node_id}, which is not in this workflow")
        return cls(node_id, slot)

    def as_input(self) -> list[str | int]:
        return [self.node_id, self.output_slot]

"""GraphStructuralValidator — does this workflow hold together on a given ComfyUI? Layer 1 of 3.

Checked against a node CATALOGUE in `/api/object_info` shape — the live box's, or the cached copy
of a box of the same version (NodeRegistryCache), which is what lets a design be checked before
any GPU exists:

  * every class exists (a missing one is usually a wrong name, sometimes a pack the box lacks)
  * a deprecated class is refused in a design the agent builds; noted in a design it was given
  * every node's input KEYS match the schema — dynamic combos' dotted keys included
    (node_input_schema) — and required inputs are present
  * every link points at a node in the graph, at an output slot that node has, of the type the
    input takes
  * every enum value is legal; every number is inside the widget's min/max and on its step grid
  * at least one output node exists (a graph with none is refused by ComfyUI: "Prompt has no
    outputs")

MODEL FILES are the one thing that depends on the box rather than the version. Against a LIVE
catalogue a model name not in the loader's list is a shopping-list item (missing_files); against
a cached one it is not judged at all — presence is Phase 2's question, on the box.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import node_input_schema
import reference_slots
from model_readiness import ModelReadiness
from workflow_link import WorkflowLink

#: Socket types that accept anything; and the v3 type that means "same type as another input".
_ANY = frozenset({"*", "COMFY_MATCHTYPE_V3"})


@dataclass
class StructuralReport:
    unknown_nodes: list[str] = field(default_factory=list)  # prose, one per node
    raw_unknown: list[str] = field(default_factory=list)  # class names, for the install gates
    missing_files: list[str] = field(default_factory=list)  # prose (live catalogue only)
    raw_missing: list[str] = field(default_factory=list)  # file names (live catalogue only)
    bad_enums: list[str] = field(default_factory=list)
    bad_links: list[str] = field(default_factory=list)
    bad_inputs: list[str] = field(default_factory=list)
    bad_values: list[str] = field(default_factory=list)  # out of range / off the step grid
    deprecated_notes: list[str] = field(default_factory=list)  # deprecated, kept: a served design
    no_output_node: bool = False

    @property
    def compiles(self) -> bool:
        return not (self.unknown_nodes or self.missing_files or self.bad_enums or self.bad_links
                    or self.bad_inputs or self.bad_values or self.no_output_node)


class GraphStructuralValidator:
    def __init__(self, catalogue: dict, live: bool) -> None:
        """:param catalogue: `/api/object_info` (or a cached copy of one).
        :param live: True when the catalogue came from the box itself — then its model lists are
        what is installed, and a model not in them is a missing file."""
        self._catalogue = catalogue or {}
        self._live = live

    def check(self, graph: dict, served: dict | None = None) -> StructuralReport:
        """:param served: {node id: {"class": ...}} for a design given as it is (a template, a
        Library workflow) — its deprecated classes are noted rather than refused."""
        report = StructuralReport()
        served = served or {}
        output_node = False
        for nid, entry in (graph or {}).items():
            if not isinstance(entry, dict):
                continue
            cls = str(entry.get("class_type") or "")
            spec = self._catalogue.get(cls)
            if not isinstance(spec, dict):
                report.unknown_nodes.append(f"node {nid}: class '{cls}' does not exist here")
                report.raw_unknown.append(cls)
                continue
            output_node = output_node or bool(spec.get("output_node"))
            if node_input_schema.deprecated(spec) and (served.get(str(nid)) or {}).get("class") == cls:
                report.deprecated_notes.append(f"node {nid} ({cls})")
            elif node_input_schema.deprecated(spec):
                report.bad_inputs.append(
                    f"node {nid}: class '{cls}' is DEPRECATED here — comfy_node_search "
                    f"'{cls}' names its successor; use that class"
                )
            schema = node_input_schema.NodeInputSchema(spec)
            for problem in schema.check(entry.get("inputs") or {}, _is_link):
                report.bad_inputs.append(f"node {nid} ({cls}): {problem}")
            self._check_values(nid, cls, spec, entry, graph, report)
        report.no_output_node = bool(graph) and not output_node and not report.raw_unknown
        return report

    # ------------------------------------------------------------------ per input

    def _check_values(self, nid, cls, spec, entry, graph, report: StructuralReport) -> None:
        specs = {
            name: s
            for section in ("required", "optional")
            for name, s in ((spec.get("input") or {}).get(section) or {}).items()
        }
        for name, value in (entry.get("inputs") or {}).items():
            try:
                link = WorkflowLink.from_input(value, graph)
            except ValueError as exc:
                report.bad_links.append(f"node {nid}.{name} {exc}")
                continue
            s = specs.get(name)
            if link is not None:
                self._check_link(nid, name, s, link, graph, report)
                continue
            if reference_slots.role_of(value) is not None:
                continue  # a reference slot — filled and checked at run time
            if not (isinstance(s, list) and s):
                continue  # dotted dynamic keys are judged by NodeInputSchema
            head, extra = s[0], (s[1] if len(s) > 1 and isinstance(s[1], dict) else {})
            if extra.get("unchecked"):
                continue
            if isinstance(head, list):
                self._check_enum(nid, cls, name, value, head, extra, report)
            elif head in ("INT", "FLOAT") and isinstance(value, (int, float)) and not isinstance(value, bool):
                self._check_number(nid, name, value, head, extra, report)

    def _check_link(self, nid, name, s, link: WorkflowLink, graph, report: StructuralReport) -> None:
        up_cls = str((graph.get(link.node_id) or {}).get("class_type") or "")
        up = self._catalogue.get(up_cls)
        if not isinstance(up, dict):
            return  # the upstream class is reported as unknown already; its outputs cannot be typed
        outs = up.get("output") or []
        if link.output_slot >= len(outs):
            report.bad_links.append(
                f"node {nid}.{name}: node {link.node_id} ({up_cls}) has no output {link.output_slot} "
                f"(it has {len(outs)})"
            )
            return
        want = s[0] if isinstance(s, list) and s and isinstance(s[0], str) else None
        got = outs[link.output_slot]
        if not want or want in _ANY or got in _ANY or isinstance(got, list):
            return
        accepted = {t.strip() for t in want.split(",")}
        if got not in accepted and not (want in ("COMFY_AUTOGROW_V3", "COMFY_DYNAMICCOMBO_V3")):
            report.bad_links.append(
                f"node {nid}.{name} takes {want}, but node {link.node_id} ({up_cls}) output "
                f"{link.output_slot} is {got}"
            )

    def _check_enum(self, nid, cls, name, value, choices, extra, report: StructuralReport) -> None:
        if not isinstance(value, str) or value in choices or ModelReadiness.spelled(value, choices):
            return
        is_file = bool(extra.get("file_folder")) or ModelReadiness.is_model_input(name, choices, value)
        if is_file and not self._live:
            return  # presence is checked on the box, not against a cached catalogue
        # A MISSING WEIGHT IS A SHOPPING-LIST ITEM, NOT AN INVALID VALUE. The field's NAME says it
        # is a weights slot; what is installed in it says nothing. On a fresh box the VAE enum is
        # one sentinel, `pixel_space`, so judged by its contents `qwen_image_vae.safetensors` read
        # as "not one of [pixel_space]" and the only legal value left rendered a raw latent.
        if is_file:
            near = ModelReadiness.closest(value, choices)
            report.missing_files.append(
                f"{value}  (for {cls}.{name}, node {nid})"
                + (f" — this machine has '{near}': if that is the same model, ask the user in one "
                   "line whether to use it before downloading anything" if near else "")
            )
            report.raw_missing.append(str(value))
            return
        legal = ", ".join(str(c) for c in choices[:8])
        report.bad_enums.append(f"node {nid}.{name}: '{value}' is not one of [{legal}…]")

    @staticmethod
    def _check_number(nid, name, value, head, extra, report: StructuralReport) -> None:
        lo, hi, step = extra.get("min"), extra.get("max"), extra.get("step")
        if isinstance(lo, (int, float)) and value < lo:
            report.bad_values.append(f"node {nid}.{name}: {value} is below the minimum {lo}")
        if isinstance(hi, (int, float)) and value > hi:
            report.bad_values.append(f"node {nid}.{name}: {value} is above the maximum {hi}")
        if head == "INT" and isinstance(step, int) and step > 1 and isinstance(lo, int):
            if (int(value) - lo) % step:
                report.bad_values.append(
                    f"node {nid}.{name}: {value} is not a legal value (from {lo} in steps of {step})"
                )


def _is_link(value) -> bool:
    return (isinstance(value, list) and len(value) == 2
            and isinstance(value[0], str) and isinstance(value[1], int))


__all__ = ["GraphStructuralValidator", "StructuralReport"]

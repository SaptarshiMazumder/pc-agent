"""NodeInputSchema — what a node's `inputs` must look like in an API-format prompt, read from
its `/object_info` entry.

WHY THIS EXISTS. `object_info` describes a node's inputs in ComfyUI's schema language, and for
the current partner nodes that language has two constructs the model kept guessing wrong:

  * `COMFY_DYNAMICCOMBO_V3` — one input (say `model`) whose VALUE is an option key, and whose
    option carries its own sub-inputs. In the API prompt those arrive FLATTENED with dotted
    names: `"model": "seedream 5.0 pro"`, `"model.size_preset": "…"`, `"model.width": 2048`.
  * `COMFY_AUTOGROW_V3` — a growable list of slots (`images`) with generated names
    (`image_1`, `image_2`, …). In the prompt each slot is `"images.image_1": ["7", 0]`, and
    nested under a dynamic combo it is `"model.images.image_1"`.

  (ComfyUI comfy_api/latest/_io.py: `finalize_prefix` joins the path with dots; DynamicCombo
  selects the option by `live_inputs[finalized_id]`; Autogrow builds `expected_id =
  finalize_prefix(curr_prefix, name)` from the template's `names` or `prefix`/`max`.)

Nothing in the raw schema says so, `comfy_validate` did not look at inputs at all, and the
server's own pre-check lets a wrong shape through to a `TypeError` at execute time. So a graph
with `"image_1"` at the top level and no `"model"` key validated clean, failed at run, and the
agent's only signal was "unexpected keyword argument" — from which it retried blind, fell back
to a deprecated node, and downgraded the model to get a run through.

This module is the fix, for EVERY node alike: it flattens any node's schema into the exact key
set the prompt must use, writes an example `inputs` block from it, and checks a node's inputs
against it — unknown keys (with the right dotted key suggested when the bare name exists deeper
in the schema), missing required keys, an option key that is not one of the options, and
sub-inputs of an option that is not the one selected. Pure functions over dicts: no I/O, so
every rule is a unit test with no instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DYNAMIC_COMBO = "COMFY_DYNAMICCOMBO_V3"
AUTOGROW = "COMFY_AUTOGROW_V3"

#: Socket types are UPPER-CASE type names (IMAGE, MODEL, LATENT, …); a widget's first element is
#: a list of choices or a primitive type name. A socket is satisfied by a link `["<node>", idx]`.
_PRIMITIVES = frozenset({"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"})


@dataclass
class InputKey:
    """One key the prompt may carry for a node."""

    key: str
    required: bool
    kind: str  # "socket" | "widget" | "combo" | "autogrow-slot"
    #: For a dynamic combo: the legal option keys. For a widget with an enum: its choices.
    choices: list[str] = field(default_factory=list)
    #: The option key this input belongs to, when it sits under a dynamic combo.
    only_when: tuple[str, str] | None = None  # (combo key, option key)
    #: A plausible example value for the example block.
    example: object = None
    #: The socket's type name, for the example's comment.
    type_name: str = ""


def _is_socket(entry) -> bool:
    return isinstance(entry, list) and bool(entry) and isinstance(entry[0], str) and entry[0] not in _PRIMITIVES and entry[0] not in (DYNAMIC_COMBO, AUTOGROW)


def _example_for(entry, name: str):
    spec = entry[1] if isinstance(entry, list) and len(entry) > 1 and isinstance(entry[1], dict) else {}
    head = entry[0] if isinstance(entry, list) and entry else None
    if isinstance(head, list):
        return head[0] if head else ""
    if "default" in spec:
        return spec["default"]
    if head == "INT":
        return int(spec.get("min", 0) or 0)
    if head == "FLOAT":
        return float(spec.get("min", 0.0) or 0.0)
    if head == "BOOLEAN":
        return False
    if head == "STRING":
        return f"<{name}>"
    if head == "COMBO":
        opts = spec.get("options") or []
        return opts[0] if opts else ""
    return None


def _walk(section: dict, prefix: list[str], required: bool, out: list[InputKey], only_when=None) -> None:
    for name, entry in (section or {}).items():
        path = prefix + [name]
        key = ".".join(path)
        head = entry[0] if isinstance(entry, list) and entry else None
        spec = entry[1] if isinstance(entry, list) and len(entry) > 1 and isinstance(entry[1], dict) else {}
        if head == DYNAMIC_COMBO:
            options = [o for o in (spec.get("options") or []) if isinstance(o, dict) and o.get("key")]
            keys = [str(o["key"]) for o in options]
            out.append(InputKey(key, required, "combo", keys, only_when, keys[0] if keys else ""))
            for o in options:
                inner = o.get("inputs") or {}
                _walk(inner.get("required") or {}, path, True, out, (key, str(o["key"])))
                _walk(inner.get("optional") or {}, path, False, out, (key, str(o["key"])))
            continue
        if head == AUTOGROW:
            template = spec.get("template") or {}
            names = template.get("names")
            if not names:
                pfx = str(template.get("prefix") or "")
                names = [f"{pfx}{i}" for i in range(int(template.get("max") or 0))]
            minimum = int(template.get("min") or 0)
            tinputs = (template.get("input") or {})
            tnames = list((tinputs.get("required") or {}).keys()) + list((tinputs.get("optional") or {}).keys())
            ttype = ""
            if tnames:
                first = (tinputs.get("required") or {}).get(tnames[0]) or (tinputs.get("optional") or {}).get(tnames[0])
                ttype = first[0] if isinstance(first, list) and first and isinstance(first[0], str) else ""
            for i, slot in enumerate(names):
                out.append(
                    InputKey(
                        ".".join(path + [str(slot)]),
                        required and i < minimum,
                        "autogrow-slot",
                        [],
                        only_when,
                        None,
                        ttype,
                    )
                )
            continue
        if _is_socket(entry):
            out.append(InputKey(key, required, "socket", [], only_when, None, str(head)))
            continue
        choices = [str(c) for c in head] if isinstance(head, list) else []
        if head == "COMBO":
            choices = [str(c) for c in (spec.get("options") or [])]
        out.append(InputKey(key, required, "widget", choices, only_when, _example_for(entry, name)))


class NodeInputSchema:
    """The prompt-side view of one node's `/object_info` entry."""

    def __init__(self, spec: dict):
        self.spec = spec if isinstance(spec, dict) else {}
        self.keys: list[InputKey] = []
        inputs = self.spec.get("input") or {}
        _walk(inputs.get("required") or {}, [], True, self.keys)
        _walk(inputs.get("optional") or {}, [], False, self.keys)

    # ------------------------------------------------------------------ lookups

    def by_key(self) -> dict[str, InputKey]:
        return {k.key: k for k in self.keys}

    @property
    def dynamic(self) -> bool:
        return any(k.kind in ("combo", "autogrow-slot") for k in self.keys)

    def _active(self, inputs: dict) -> list[InputKey]:
        """The keys that apply given the option each dynamic combo currently selects."""
        active = []
        for k in self.keys:
            if k.only_when is None:
                active.append(k)
                continue
            combo, option = k.only_when
            chosen = inputs.get(combo)
            # A nested combo's own selection is read from the prompt too.
            if chosen == option:
                active.append(k)
        return active

    # ------------------------------------------------------------------ example

    def example(self, node_id_hint: str = "<node id>") -> dict:
        """An `inputs` block in API format: the first option of every dynamic combo, its
        sub-inputs, the first autogrow slot, defaults for widgets, and a link placeholder for
        every socket. Meant to be read, then edited — not submitted as is."""
        out: dict = {}
        selected: dict[str, str] = {}
        for k in self.keys:
            if k.kind == "combo":
                selected[k.key] = k.choices[0] if k.choices else ""
        for k in self.keys:
            if k.only_when is not None:
                combo, option = k.only_when
                if selected.get(combo) != option:
                    continue
            if k.kind == "combo":
                out[k.key] = selected.get(k.key, "")
            elif k.kind == "socket":
                if k.required:
                    out[k.key] = [node_id_hint, 0]
            elif k.kind == "autogrow-slot":
                # Only the first slot of each list, so the example stays short.
                if k.key.rsplit(".", 1)[-1] == self._first_slot_name(k.key):
                    out[k.key] = [node_id_hint, 0]
            else:
                if k.required or k.example not in (None, ""):
                    out[k.key] = k.example
        return out

    def _first_slot_name(self, key: str) -> str:
        prefix = key.rsplit(".", 1)[0]
        for k in self.keys:
            if k.kind == "autogrow-slot" and k.key.rsplit(".", 1)[0] == prefix:
                return k.key.rsplit(".", 1)[-1]
        return ""

    # ------------------------------------------------------------------ check

    def check(self, inputs: dict, is_link) -> list[str]:
        """Problems with one node's `inputs`, in the order they should be fixed.

        `is_link(value)` says whether a value is a link to another node's output. Checked:
        unknown keys (with the dotted key suggested when the bare name exists deeper in the
        schema, and the schema key suggested when the prompt used a dotted name the node does
        not have); a dynamic combo whose value is not one of its options; sub-inputs of an
        option that is not selected; required keys that are missing.
        """
        inputs = inputs if isinstance(inputs, dict) else {}
        problems: list[str] = []
        all_keys = self.by_key()
        active = {k.key: k for k in self._active(inputs)}

        # Dynamic combos: the selected option must exist.
        for k in self.keys:
            if k.kind != "combo" or k.key not in inputs:
                continue
            value = inputs[k.key]
            if not isinstance(value, str) or value not in k.choices:
                problems.append(
                    f"'{k.key}' must be one of {k.choices}, not {value!r}"
                )

        # Unknown keys, and sub-inputs of a non-selected option.
        for name in inputs:
            if name in active:
                continue
            if name in all_keys:
                k = all_keys[name]
                if k.only_when is not None:
                    combo, option = k.only_when
                    problems.append(
                        f"'{name}' belongs to option '{option}' of '{combo}', but '{combo}' is "
                        f"{inputs.get(combo)!r}"
                    )
                    continue
            # Suggest the dotted form when the bare name exists deeper in the schema.
            tail = name.rsplit(".", 1)[-1]
            candidates = [k.key for k in self.keys if k.key.rsplit(".", 1)[-1] == tail and k.key != name]
            hint = f" — did you mean '{candidates[0]}'?" if candidates else ""
            problems.append(f"'{name}' is not an input of this node{hint}")

        # Required keys that are missing. A required socket is satisfied by a link only.
        for key, k in active.items():
            if not k.required or key in inputs:
                continue
            if k.kind == "socket":
                problems.append(f"'{key}' ({k.type_name}) is required and must be wired to another node")
            elif k.kind == "combo":
                problems.append(f"'{key}' is required: one of {k.choices}")
            elif k.kind == "autogrow-slot":
                problems.append(f"'{key}' is required")
            else:
                problems.append(f"'{key}' is required")

        # A socket given a literal, or a widget given a link, is a wiring error.
        for name, value in inputs.items():
            k = active.get(name)
            if k is None:
                continue
            linked = bool(is_link(value))
            if k.kind == "socket" and not linked:
                problems.append(f"'{name}' ({k.type_name}) must be a link [node_id, output_index], not {value!r}")
            if k.kind == "autogrow-slot" and not linked:
                problems.append(f"'{name}' is a slot for an {k.type_name or 'input'} link, not {value!r}")
        return problems


def deprecated(spec: dict) -> bool:
    """Whether `/object_info` marks this class as deprecated (a successor exists)."""
    return bool(isinstance(spec, dict) and spec.get("deprecated"))

"""comfy_nodes — which node classes this server actually has, and their exact inputs.

THE FAILURE THIS PREVENTS. Node class names are strings, they change between ComfyUI versions,
and half of them come from custom node packs that may or may not be installed on the box in
question. An agent working from memory writes `"class_type": "KSamplerAdvanced"` on a server that
has it, and `"class_type": "SamplerCustomAdvanced"` on one that does not, and the failure arrives
as a 400 from the queue several steps later — or worse, as a workflow the user cannot import.

Every string this returns came from the server. Build only from these.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from comfy_client import ComfyClient, ComfyError
from object_info_cache import ObjectInfoCache

#: Enum inputs list every installed file — a checkpoint dropdown on a full server is hundreds of
#: lines and would bury the input NAMES, which is what the caller came for. `comfy_models` is the
#: tool for the full list.
ENUM_PREVIEW = 12


class ComfyNodesTool(Tool):
    name = "comfy_nodes"
    label = "Comfy Nodes"
    default_retryable = True
    description = (
        "Look up node classes installed on the ComfyUI server: search by name/category, or ask "
        "for one class and get its exact required and optional inputs with their types and "
        "allowed values. USE THIS BEFORE WRITING ANY NODE into a workflow — a class name from "
        "memory is a guess, and a graph built on a node this server does not have fails at queue "
        "time or imports broken. Pass `refresh` after the user installs a custom node pack."
    )
    parameters = {
        "type": "object",
        "properties": {
            "node": {
                "type": "string",
                "description": "Exact class name, e.g. 'KSampler'. Returns its full input schema.",
            },
            "search": {
                "type": "string",
                "description": "Substring matched against class name, display name and category.",
            },
            "refresh": {
                "type": "boolean",
                "description": "Re-fetch the catalogue (after installing custom nodes).",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        node = str(params.get("node") or "").strip()
        search = str(params.get("search") or "").strip().lower()
        refresh = bool(params.get("refresh"))

        try:
            client = ComfyClient.from_settings()
            if node and not refresh:
                found = await ObjectInfoCache.node(client, node)
                if not found:
                    return await self._not_installed(client, node)
                return ToolResult.text(_describe(found))
            catalogue = await ObjectInfoCache.all(client, refresh=refresh)
        except ComfyError as e:
            return ToolResult.text(str(e), is_error=True)
        except RuntimeError as e:
            return ToolResult.text(str(e), is_error=True)

        if node:
            found = catalogue.get(node)
            if not found:
                return await self._not_installed(client, node)
            return ToolResult.text(_describe(found))

        if not search:
            cats: dict[str, int] = {}
            for spec in catalogue.values():
                cats[str(spec.get("category") or "uncategorised")] = (
                    cats.get(str(spec.get("category") or "uncategorised"), 0) + 1
                )
            top = sorted(cats.items(), key=lambda kv: -kv[1])[:25]
            return ToolResult.text(
                f"{len(catalogue)} node classes installed on {client.base}.\n"
                f"Largest categories:\n"
                + "\n".join(f"- {name} ({n})" for name, n in top)
                + "\n\nSearch with `search`, or ask for one class with `node`."
            )

        hits = [
            (name, spec)
            for name, spec in catalogue.items()
            if search in name.lower()
            or search in str(spec.get("display_name") or "").lower()
            or search in str(spec.get("category") or "").lower()
        ]
        if not hits:
            return ToolResult.text(
                f"No node on {client.base} matches {search!r}. It may need a custom node pack "
                f"installed on that machine — check comfy_server for an SSH target."
            )
        rows = [
            f"- {name}  ({spec.get('display_name') or name}) · {spec.get('category') or '?'}"
            + ("  [deprecated]" if spec.get("deprecated") else "")
            for name, spec in sorted(hits)[:60]
        ]
        return ToolResult.text(
            f"{len(hits)} match(es) for {search!r}:\n" + "\n".join(rows)
            + (f"\n… and {len(hits) - 60} more — narrow the search." if len(hits) > 60 else "")
            + "\n\nAsk for one with `node` to get its exact inputs."
        )

    @staticmethod
    async def _not_installed(client: ComfyClient, node: str) -> ToolResult:
        return ToolResult.text(
            f"{client.base} has no node class named {node!r}. Do not write it into a workflow. "
            f"Search for what this server does have (`search`), or install the pack that "
            f"provides it over SSH — see comfy_server.",
            is_error=True,
        )


def _describe(spec: dict) -> str:
    name = spec.get("name") or "?"
    lines = [
        f"{name}  ({spec.get('display_name') or name})",
        f"category: {spec.get('category') or '?'}"
        + ("  [DEPRECATED]" if spec.get("deprecated") else "")
        + ("  [EXPERIMENTAL]" if spec.get("experimental") else ""),
    ]
    if spec.get("description"):
        lines.append(str(spec["description"]).strip())

    inputs = spec.get("input") or {}
    for section in ("required", "optional"):
        fields = inputs.get(section) or {}
        if not fields:
            continue
        lines.append(f"\n{section} inputs:")
        for key, decl in fields.items():
            lines.append(f"  {key}: {_input_type(decl)}")

    outputs = spec.get("output") or []
    names = spec.get("output_name") or []
    if outputs:
        pairs = [f"{names[i] if i < len(names) else t}:{t}" for i, t in enumerate(outputs)]
        lines.append("\noutputs: " + ", ".join(pairs))
    if spec.get("output_node"):
        lines.append("This is an OUTPUT node — a workflow needs at least one to produce anything.")
    return "\n".join(lines)


def _input_type(decl) -> str:
    """An input is `[type, options?]`, where `type` is either a type name ("INT", "MODEL") or the
    LIST OF ALLOWED VALUES — which is how ComfyUI advertises installed checkpoints, samplers and
    schedulers. The allowed values are the useful half, so they are shown."""
    if not isinstance(decl, list) or not decl:
        return str(decl)
    kind, options = decl[0], (decl[1] if len(decl) > 1 and isinstance(decl[1], dict) else {})
    extra = []
    if "default" in options:
        extra.append(f"default={options['default']!r}")
    if "min" in options or "max" in options:
        extra.append(f"range={options.get('min')}..{options.get('max')}")
    suffix = f"  ({', '.join(extra)})" if extra else ""

    if isinstance(kind, list):
        shown = [str(v) for v in kind[:ENUM_PREVIEW]]
        rest = f" … +{len(kind) - ENUM_PREVIEW} more" if len(kind) > ENUM_PREVIEW else ""
        return f"one of [{', '.join(shown)}{rest}]{suffix}"
    return f"{kind}{suffix}"

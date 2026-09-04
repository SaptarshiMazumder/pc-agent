"""comfy_research — what does this model need? Answered from the internet, verified on the box.

THE PROBLEM IT SOLVES. A workflow's shape is dictated by its model's architecture, and
architectures differ completely: a Flux-family model wants a bare-unet loader, two text
encoders and its own VAE at cfg 1.0; an SDXL checkpoint is self-contained at cfg ~7; a turbo
distillation wants 4 steps; next month's release wants something nobody has written down yet.
None of that is in ComfyUI's node specs, and this agent is FORBIDDEN from reciting it from
memory — training-data recall is how a workflow gets built for the model that existed a year
ago. So the answer is fetched from the people who published the model, at the moment it is
needed.

NOTHING MODEL-SPECIFIC LIVES HERE. The tool searches and fetches; deciding what a README means
is the agent's job. The one piece of interpretation it does — checking names against the
instance — is mechanical: a node class either exists in `/object_info` or it does not.

CREDENTIALS ARE RETRY-ONLY. Public models need no token, and an unset secret substitutes to a
literal `${NAME}` that providers reject — so every request goes out BARE first, and the token
header is added only on a 401/403, which is exactly the gated-model case it exists for.
"""

from __future__ import annotations

import json

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.infrastructure.net.outbound import fetch

from comfy_bridge import _get as _instance_get

#: How much fetched text rides back to the model. Enough for any README or workflow JSON that
#: matters; a page bigger than this is a webpage, and its useful part is at the top.
_CLAMP = 14_000


def _web_get(url: str, token_placeholder: str | None = None, timeout_s: float = 30.0):
    """GET a public URL; on 401/403 retry once carrying the named token.

    Bare-first is load-bearing, not politeness — see the module docstring: an UNSET secret
    would otherwise poison every public request with a literal `${NAME}` header.
    """
    res = fetch(url, timeout_s=timeout_s)
    if res.status in (401, 403) and token_placeholder:
        res = fetch(
            url,
            headers={"Authorization": f"Bearer {token_placeholder}"},
            timeout_s=timeout_s,
        )
    return res


def _hf_search(query: str) -> list[dict]:
    """Top Hugging Face hits for a model name — id, downloads, and what files each ships."""
    res = _web_get(
        "https://huggingface.co/api/models?search="
        + query.replace(" ", "+")
        + "&limit=5&full=true"
    )
    if not res.ok:
        return []
    try:
        rows = res.json()
    except ValueError:
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        files = [s.get("rfilename", "") for s in (row.get("siblings") or [])]
        out.append(
            {
                "id": row.get("id", ""),
                "downloads": row.get("downloads", 0),
                "pipeline": row.get("pipeline_tag", ""),
                "gated": bool(row.get("gated")),
                # The two file kinds that answer questions: weights (what to install, where its
                # size hints at what it is) and any shipped .json (very often a REFERENCE
                # WORKFLOW — the publisher's own answer to "how do I run this").
                "weights": [f for f in files if f.lower().endswith((".safetensors", ".gguf"))][:12],
                "json_files": [f for f in files if f.lower().endswith(".json")][:12],
            }
        )
    return out


def _civitai_search(query: str) -> list[dict]:
    """Top Civitai hits — where fine-tunes and LoRAs live that HF often does not have."""
    res = _web_get(
        "https://civitai.com/api/v1/models?query=" + query.replace(" ", "%20") + "&limit=3",
        token_placeholder="${CIVITAI_TOKEN}",
    )
    if not res.ok:
        return []
    try:
        items = (res.json() or {}).get("items") or []
    except ValueError:
        return []
    out = []
    for row in items:
        versions = row.get("modelVersions") or []
        v0 = versions[0] if versions else {}
        out.append(
            {
                "name": row.get("name", ""),
                "type": row.get("type", ""),  # Checkpoint / LORA / ControlNet …
                "base_model": v0.get("baseModel", ""),  # the fact that decides the graph
                "files": [f.get("name", "") for f in (v0.get("files") or [])][:6],
                "page": f"https://civitai.com/models/{row.get('id', '')}",
            }
        )
    return out


def _check_instance(names: list[str]) -> dict:
    """Which of these node classes / model files exist on the user's instance, RIGHT NOW.

    One catalogue read answers both kinds of name: a node class is a key of `/object_info`; a
    filename appears in some loader's enum. This is the step that turns research into a plan —
    what the publisher says is needed, minus what is present, is the user's shopping list.
    """
    res = _instance_get("/api/object_info", timeout_s=60.0)
    if not res.ok:
        return {"error": f"could not read the instance ({res.error or res.status})"}
    try:
        catalogue = res.json()
    except ValueError:
        return {"error": "the instance's node catalogue is too large to fetch whole"}

    all_files: set[str] = set()
    for spec in catalogue.values():
        if not isinstance(spec, dict):
            continue
        inputs = spec.get("input") or {}
        for section in ("required", "optional"):
            for entry in (inputs.get(section) or {}).values():
                values = entry[0] if isinstance(entry, list) and entry else None
                if isinstance(values, list):
                    all_files.update(v for v in values if isinstance(v, str))

    report = {}
    for name in names:
        if name in catalogue:
            report[name] = "node: installed"
        elif name in all_files:
            report[name] = "file: present"
        else:
            # A filename is usually referenced bare while research names it with a path.
            tail = name.rsplit("/", 1)[-1].lower()
            close = [f for f in all_files if f.rsplit("/", 1)[-1].lower() == tail]
            report[name] = f"MISSING (closest present: {close[0]})" if close else "MISSING"
    return report


class ComfyResearchTool(Tool):
    name = "comfy_research"
    label = "Research a model"
    default_retryable = True
    description = (
        "Find out how a model actually works before designing around it. Give it a model name "
        "to SEARCH (Hugging Face + Civitai: repos, base model, weight files, and any reference "
        "workflow JSONs the publisher ships), or a URL to FETCH whole (a model card, a shipped "
        "workflow, a docs page — the publisher's own reference workflow is the single best "
        "answer to 'what does this need'). Pass `check` to verify names — node classes or "
        "model filenames — against the user's instance, turning research into a concrete "
        "present/missing list. Use it for EVERY model family you have not already confirmed "
        "this session, and again whenever the user changes model mid-job."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A model name to search ('qwen image', 'z-image-turbo') or a "
                "URL to fetch (an hf.co README, a raw workflow .json, a docs page).",
            },
            "check": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Node class names and/or model filenames to verify against the "
                "user's instance. May be used alone or with a query.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            query = str(params.get("query") or "").strip()
            check = [str(n).strip() for n in (params.get("check") or []) if str(n).strip()]
            if not query and not check:
                return ToolResult.text("give a query, a URL, or names to check", is_error=True)

            sections: list[str] = []
            details: dict = {}

            if query.lower().startswith(("http://", "https://")):
                # HF file/card URLs are the ones that turn out gated, hence the HF token here.
                res = _web_get(query, token_placeholder="${HF_TOKEN}", timeout_s=45.0)
                if not res.ok:
                    hint = (
                        " (gated — the user needs to accept the model's license on the site "
                        "and put a token in this agent's settings)"
                        if res.status in (401, 403)
                        else ""
                    )
                    sections.append(f"fetch failed: HTTP {res.status} {res.error or ''}{hint}")
                else:
                    body = res.text or ""
                    try:  # workflow JSONs render better compact than as one line
                        body = json.dumps(res.json(), indent=1)
                    except ValueError:
                        pass
                    truncated = len(body) > _CLAMP
                    sections.append(body[:_CLAMP] + ("\n…(truncated)" if truncated else ""))
                    details["fetched"] = {"url": query, "truncated": truncated}
            elif query:
                hf = _hf_search(query)
                cv = _civitai_search(query)
                details["huggingface"], details["civitai"] = hf, cv
                if hf:
                    lines = ["Hugging Face:"]
                    for r in hf:
                        gated = " [GATED]" if r["gated"] else ""
                        lines.append(
                            f"  {r['id']}{gated} ({r['downloads']:,} downloads, "
                            f"{r['pipeline'] or 'no pipeline tag'})"
                        )
                        if r["json_files"]:
                            lines.append(
                                f"    ships JSON (likely reference workflows): "
                                + ", ".join(r["json_files"])
                            )
                        if r["weights"]:
                            lines.append(f"    weights: " + ", ".join(r["weights"][:6]))
                    lines.append(
                        "  read a card:  https://huggingface.co/<id>/raw/main/README.md   "
                        "a file: https://huggingface.co/<id>/resolve/main/<file>"
                    )
                    sections.append("\n".join(lines))
                if cv:
                    lines = ["Civitai:"]
                    for r in cv:
                        lines.append(
                            f"  {r['name']} ({r['type']}, base: {r['base_model'] or '?'}) "
                            f"{r['page']}"
                        )
                    sections.append("\n".join(lines))
                if not hf and not cv:
                    sections.append(
                        f"nothing on Hugging Face or Civitai for {query!r}. Try the exact "
                        "repo/file name, or web_search the release announcement and fetch its "
                        "URL here."
                    )

            if check:
                report = _check_instance(check)
                details["instance"] = report
                sections.append(
                    "on the user's instance:\n"
                    + "\n".join(f"  {k}: {v}" for k, v in report.items())
                )

            return ToolResult.text("\n\n".join(sections), details=details)
        except Exception as e:  # noqa: BLE001 — a tool reports, it does not crash the turn
            return ToolResult.text(f"comfy_research failed: {type(e).__name__}: {e}", is_error=True)

"""imagegen_templates — the art-template ENGINE (data lives in the agent, logic lives here).

An art template is a `*.toml` file in the agent's `templates/` folder (see
agents/figure-creator/templates/README.md). Each is a reusable, art-directed style recipe: a rich
`{subject}`-slotted prompt plus palette / negative / aspect / model / exemplar refs. This module
DISCOVERS those files (workspace-relative, zero hardcoded styles), and RESOLVES one against a subject
into the concrete inputs generate_artwork needs.

Non-hardcoded by design: add/edit a `.toml` and it is live — no code change. Pure stdlib (tomllib) +
the tiny workspace helper, so it stays trivially testable and never pulls heavy deps.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agentd.application.run_context import current_workspace


def _candidate_dirs(config) -> list[Path]:
    """Where to look for templates, highest priority first: an explicit `templates_dir` knob, then the
    conventional agent locations relative to the per-run workspace. For figure-creator the workspace is
    `agents/figure-creator/workspace`, so `<ws>/../templates` is the agent's own `templates/` folder."""
    dirs: list[Path] = []
    knob = None
    try:
        from agentd.application.tool_models import tool_config
        knob = tool_config(config, "imagegen", "generate_artwork", "templates_dir", default=None)
    except Exception:
        knob = None
    ws = Path(current_workspace(str(getattr(config, "workspace", "."))) or ".")
    if knob:
        p = Path(knob)
        dirs.append(p if p.is_absolute() else ws / p)
    dirs.append(ws / "templates")
    dirs.append(ws.parent / "templates")           # <agent>/templates when ws == <agent>/workspace
    # de-dup while preserving order
    seen, out = set(), []
    for d in dirs:
        try:
            key = d.resolve()
        except Exception:
            key = d
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def load_templates(config) -> dict[str, dict]:
    """All discoverable templates keyed by id. First-found (higher-priority dir) wins on id clash, so a
    template dropped in the agent's folder can shadow a lower-priority one. A malformed .toml is skipped,
    never fatal."""
    out: dict[str, dict] = {}
    for d in _candidate_dirs(config):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.toml")):
            try:
                data = tomllib.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            tid = str(data.get("id") or f.stem).strip()
            if not tid or "prompt" not in data or tid in out:
                continue
            data["id"] = tid
            data["_dir"] = str(d)
            out[tid] = data
    return out


def get_template(config, template_id: str) -> dict | None:
    return load_templates(config).get(str(template_id or "").strip())


def catalog(config) -> list[dict]:
    """The gallery view: one compact entry per template for list_templates / the agent to browse."""
    items = []
    for t in load_templates(config).values():
        items.append({
            "id": t["id"],
            "name": t.get("name", t["id"]),
            "description": t.get("description", ""),
            "when_to_use": t.get("when_to_use", ""),
            "tags": list(t.get("tags", []) or []),
            "palette": list(t.get("palette", []) or []),
            "aspect": t.get("aspect"),
            "resolution": t.get("resolution"),
            "has_exemplars": bool(t.get("exemplars")),
        })
    return sorted(items, key=lambda x: x["id"])


def palette_directive(palette, locked: bool = False) -> str:
    """Turn a palette into a prompt directive — a hard constraint when locked, a nudge otherwise."""
    cols = ", ".join(str(c) for c in (palette or []) if str(c).strip())
    if not cols:
        return ""
    if locked:
        return f" Use ONLY this colour palette: {cols}. Do not introduce other hues."
    return f" Favour this cohesive colour palette: {cols}."


def resolve(config, template_id: str, subject: str, palette_override=None) -> dict:
    """Merge a subject into a template -> the concrete generation inputs. Raises KeyError (with the
    available ids) for an unknown template so the agent can correct itself."""
    t = get_template(config, template_id)
    if not t:
        known = ", ".join(sorted(load_templates(config))) or "(none found)"
        raise KeyError(f"unknown art template {template_id!r}. Available: {known}")
    subject = (subject or "").strip()
    base = str(t["prompt"]).strip()
    prompt = base.replace("{subject}", subject) if "{subject}" in base else f"{base} Subject: {subject}."
    palette = list(palette_override) if palette_override else list(t.get("palette", []) or [])
    tdir = Path(t.get("_dir", "."))
    exemplars = []
    for e in (t.get("exemplars", []) or []):
        p = Path(e)
        exemplars.append(str(p if p.is_absolute() else tdir / p))
    return {
        "id": t["id"],
        "name": t.get("name", t["id"]),
        "prompt": prompt,
        "negative": str(t.get("negative", "") or ""),
        "palette": palette,
        "palette_locked": bool(t.get("palette_locked", False)),
        "aspect": t.get("aspect"),
        "resolution": t.get("resolution"),
        "provider": t.get("provider"),
        "model": t.get("model"),
        "exemplars": exemplars,
    }

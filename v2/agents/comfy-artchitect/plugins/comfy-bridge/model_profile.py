"""A MODEL PROFILE — what is known about running one model family well, as data.

One file per family in `model_profiles/` (e.g. minimax-h3.md): a fenced JSON header, then the
family's prompt guide as plain text. The header holds

    nodes     the node types that mean "this workflow runs this family"
    prompt    the prompt input and the markers its official format requires
    weights   per slot (video model, text encoder…) every file, best first, with the VRAM it needs
              and, for a format only some cards run fast, those cards (`best_on_gpu`: name
              fragments, e.g. "RTX 50") — elsewhere it still runs, and a file that fits is preferred
    download  the link pattern for a file ({kind}, {file})
    settings  node inputs that make it look right, each with why
    pitfalls  what makes results worse, in words

WHY DATA. Everything learned about MiniMax H3 in one day — its six-section prompt, that the pruned
weights are the 12 GB-card version, that NVFP4 only runs natively on Blackwell, that `max` references
and the beta scheduler keep faces — lived in a module, a template's notes and an About page, and the
next workflow started from the weak defaults again. A profile is where it goes instead; adding a
family is adding a file.

ADVICE, NEVER A GATE. `notes` says what differs from the profile for THIS machine; changing a
template's model or settings stays the user's call (the ask).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADER = re.compile(r"^\s*```json\s*\n(.*?)\n```\s*\n?(.*)$", re.S)


@dataclass
class ModelProfile:
    family: str
    nodes: list[str]
    prompt_input: str = ""
    prompt_required: list[str] = field(default_factory=list)
    guide: str = ""
    weights: list[dict] = field(default_factory=list)
    download: str = ""
    settings: list[dict] = field(default_factory=list)
    pitfalls: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "ModelProfile":
        """A profile file; ValueError when it is not one (a broken profile is said, not skipped)."""
        match = _HEADER.match(Path(path).read_text(encoding="utf-8"))
        if not match:
            raise ValueError(f"{Path(path).name}: a model profile starts with a ```json header")
        head = json.loads(match.group(1))
        if not head.get("family") or not head.get("nodes"):
            raise ValueError(f"{Path(path).name}: a model profile needs `family` and `nodes`")
        prompt = head.get("prompt") or {}
        return cls(family=str(head["family"]), nodes=[str(n) for n in head["nodes"]],
                   prompt_input=str(prompt.get("input") or ""),
                   prompt_required=[str(r) for r in prompt.get("required") or []],
                   guide=match.group(2).strip(), weights=list(head.get("weights") or []),
                   download=str(head.get("download") or ""), settings=list(head.get("settings") or []),
                   pitfalls=[str(p) for p in head.get("pitfalls") or []])

    def applies(self, graph: dict) -> bool:
        return any(isinstance(n, dict) and n.get("class_type") in self.nodes for n in graph.values())

    def notes(self, graph: dict, gpu_name: str, vram_gb: float, fixed_design: bool = False) -> list[str]:
        """What differs from this profile, for a machine with this GPU. A FIXED design (a template's
        or Library workflow's) keeps its own weights and settings — only the prompt format is said."""
        if not self.applies(graph):
            return []
        setup = [] if fixed_design else self._weight_notes(graph, gpu_name, vram_gb) + self._setting_notes(graph)
        return setup + self._prompt_notes(graph)

    # ── weights ──────────────────────────────────────────────────────────────────────────

    def _weight_notes(self, graph: dict, gpu_name: str, vram_gb: float) -> list[str]:
        used = {_base(v) for n in graph.values() if isinstance(n, dict)
                for v in (n.get("inputs") or {}).values() if isinstance(v, str)}
        out = []
        for slot in self.weights:
            files = slot.get("files") or []
            current = next((f for f in files if f["file"].lower() in used), None)
            if current is None:
                continue
            fits = [f for f in files if f.get("min_vram", 0) <= vram_gb]
            # A format made for other cards comes last among what fits: it runs, slower.
            fits = [f for f in fits if _gpu_ok(f, gpu_name)] + [f for f in fits if not _gpu_ok(f, gpu_name)]
            best = fits[0] if fits else None
            if best is None:
                out.append(f"{slot['slot']}: no {self.family} file in the profile fits a {vram_gb:.0f} GB "
                           f"{gpu_name or 'GPU'}; the smallest needs {files[-1].get('min_vram')} GB.")
            elif not _gpu_ok(current, gpu_name) and _gpu_ok(best, gpu_name) and best is not current:
                out.append(f"{slot['slot']}: {current['file']} — {current.get('note', '')}; this machine is "
                           f"a {gpu_name}. Use {self._offer(slot, best)}.")
            elif current.get("min_vram", 0) > vram_gb:
                out.append(f"{slot['slot']}: {current['file']} needs about {current['min_vram']} GB and this "
                           f"machine has {vram_gb:.0f} GB. Use {self._offer(slot, best)}.")
            elif files.index(best) < files.index(current) and best.get("file") != current.get("same_as") \
                    and current.get("file") != best.get("same_as"):
                out.append(f"{slot['slot']}: {current['file']} ({current.get('note', '')}) — this "
                           f"{vram_gb:.0f} GB machine runs a better one: {self._offer(slot, best)}.")
        return out

    def _offer(self, slot: dict, f: dict) -> str:
        link = self.download.format(kind=slot.get("kind", ""), file=f["file"]) if self.download else ""
        return f"{f['file']} ({f.get('gb', '?')} GB, {f.get('note', '')}){' — ' + link if link else ''}"

    # ── settings ─────────────────────────────────────────────────────────────────────────

    def _setting_notes(self, graph: dict) -> list[str]:
        out = []
        for s in self.settings:
            for nid, node in graph.items():
                if not isinstance(node, dict) or node.get("class_type") != s.get("node"):
                    continue
                value = (node.get("inputs") or {}).get(s.get("input"))
                if value is None or isinstance(value, list) or _same(value, s.get("value"), s.get("within", 0)):
                    continue
                out.append(f"node {nid} {s['node']}.{s['input']} is {value!r}; {self.family} looks right "
                           f"with {s['value']!r} — {s.get('why', '')}.")
        return out

    # ── prompt ───────────────────────────────────────────────────────────────────────────

    def _prompt_notes(self, graph: dict) -> list[str]:
        if not self.prompt_required:
            return []
        for nid, node in graph.items():
            if not isinstance(node, dict) or node.get("class_type") not in self.nodes:
                continue
            text = _text_of(graph, (node.get("inputs") or {}).get(self.prompt_input))
            if text is None:
                continue
            missing = [r for r in self.prompt_required if r.lower() not in text.lower()]
            if missing:
                return [f"the prompt of node {nid} is not in {self.family}'s own format (missing: "
                        f"{', '.join(missing)}). When you write or change it, use this format — a "
                        f"template or Library workflow run AS IT IS keeps its prompt.\n{self.guide}"]
        return []


def _base(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _gpu_ok(f: dict, gpu_name: str) -> bool:
    needs = f.get("best_on_gpu") or []
    return not needs or any(k.lower() in (gpu_name or "").lower() for k in needs)


def _same(a, b, within: float = 0) -> bool:
    try:
        return abs(float(a) - float(b)) <= within
    except (TypeError, ValueError):
        return str(a) == str(b)


def _text_of(graph: dict, value) -> str | None:
    """The text an input receives — written on it, or wired from a text node (a short chain)."""
    for _ in range(4):
        if isinstance(value, str):
            return value
        if not (isinstance(value, list) and value and str(value[0]) in graph):
            return None
        inputs = graph[str(value[0])].get("inputs") or {}
        value = next((v for k, v in inputs.items() if k in ("value", "text", "string", "prompt")), None)
    return value if isinstance(value, str) else None

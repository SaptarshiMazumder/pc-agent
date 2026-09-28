"""A template's SETUP GUIDE — `setup.json` beside `template.json`: every node pack and model file
the template's workflows need, each with where it comes from.

WHY IT IS MANDATORY. A template used to carry only its workflows' installer lists, and those are
guesses from what one machine happened to report: the Krea/LTX template listed 7 models and 1
node pack and marked 2 models and 2 packs "source unknown". The agent filled the holes itself —
searched, guessed a Hugging Face link that does not exist, rewrote the workflow around the
missing pieces — and forty minutes later had rendered nothing. A template is a promise that it
runs as it is; the guide is what makes that promise checkable. The agent works something out
ONLY where the guide says nothing.

    {"format": "comfy-penguin-setup", "version": 1,
     "node_packs": [{"name", "repository", "classes": [node class, …]}],
     "models":     [{"filename", "kind", "url"}],
     "notes": "…"}

`kind` is the models/ folder (diffusion_models, loras, vae, …); `filename` may carry a subfolder
(Krea2/lora.safetensors). A pack's `classes` are the node types it provides — what a missing
node is matched against; a pack from an installer list may leave it empty.

This module is the model and its checks only: no I/O beyond reading the file, no machine.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

FORMAT = "comfy-penguin-setup"
VERSION = 1
FILE = "setup.json"

#: The unresolved lines WorkflowInstallerManifest writes, read back as gaps.
_MODEL_GAP = re.compile(r"^Model source/destination for ([^.]+)\.(\w+): (.+)$")
_PACK_GAP = re.compile(r"^Node pack source for (.+)$")
_ORIGIN_GAP = re.compile(r"^Node origin for (\S+) \(not confirmed as built-in\)$")
#: A loader field → the models/ folder it reads (ModelReadiness.DIRECTORY_FIELDS, inverted).
_FIELD_KIND = {
    "ckpt_name": "checkpoints", "unet_name": "diffusion_models", "vae_name": "vae",
    "clip_name": "text_encoders", "clip_name1": "text_encoders", "clip_name2": "text_encoders",
    "clip_name3": "text_encoders", "lora_name": "loras", "control_net_name": "controlnet",
    "upscale_model_name": "upscale_models",
}


@dataclass
class NodePackSource:
    name: str
    repository: str
    classes: list[str] = field(default_factory=list)


@dataclass
class ModelSource:
    filename: str
    kind: str
    url: str


@dataclass
class SetupGap:
    """Something a workflow needs that the guide does not say where to get."""
    type: str  # "model" | "node_pack"
    name: str  # the model's filename, or the node class
    kind: str = ""  # for a model: its models/ folder, when the field names one
    used_by: str = ""  # "Node.field" or the node class


class TemplateSetupGuide:
    def __init__(self, node_packs: list[NodePackSource], models: list[ModelSource], notes: str = "") -> None:
        self.node_packs = node_packs
        self.models = models
        self.notes = notes

    # ── reading ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, folder: Path) -> tuple["TemplateSetupGuide | None", str]:
        """The guide in a template folder, or None and why not (missing is a reason, not an error:
        the caller decides what a template without one may do)."""
        path = Path(folder) / FILE
        if not path.is_file():
            return None, f"it has no {FILE} (setup guide)"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return None, f"its {FILE} could not be read: {e}"
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data) -> tuple["TemplateSetupGuide | None", str]:
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            return None, f"{FILE} is not a {FORMAT}"
        if int(data.get("version") or 0) > VERSION:
            return None, f"{FILE} was made by a newer app (version {data.get('version')})"
        packs = []
        for p in data.get("node_packs") or []:
            if not isinstance(p, dict) or not str(p.get("repository") or "").startswith("https://"):
                return None, f"a node pack in {FILE} has no https repository: {p!r}"
            packs.append(NodePackSource(name=str(p.get("name") or p["repository"].rstrip("/").rsplit("/", 1)[-1]),
                                        repository=str(p["repository"]),
                                        classes=[str(c) for c in p.get("classes") or []]))
        models = []
        for m in data.get("models") or []:
            if (not isinstance(m, dict) or not m.get("filename") or not m.get("kind")
                    or not str(m.get("url") or "").startswith("https://")):
                return None, f"a model in {FILE} needs filename, kind and an https url: {m!r}"
            models.append(ModelSource(filename=str(m["filename"]).replace("\\", "/"), kind=str(m["kind"]),
                                      url=str(m["url"])))
        return cls(packs, models, str(data.get("notes") or "")), ""

    def to_dict(self) -> dict:
        return {"format": FORMAT, "version": VERSION,
                "node_packs": [asdict(p) for p in self.node_packs],
                "models": [asdict(m) for m in self.models],
                **({"notes": self.notes} if self.notes else {})}

    # ── what it covers ───────────────────────────────────────────────────────────────────

    def covers_model(self, filename: str) -> bool:
        """The same file whatever its slash or subfolder — as ModelReadiness matches names."""
        want = filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
        return any(m.filename.rsplit("/", 1)[-1].lower() == want for m in self.models)

    def covers_class(self, node_class: str) -> bool:
        return any(node_class in p.classes for p in self.node_packs)

    def gaps(self, installer_manifests: list[dict]) -> list[SetupGap]:
        """What the workflows' installer lists could not source and this guide does not either."""
        found: dict[tuple[str, str], SetupGap] = {}
        for manifest in installer_manifests:
            for line in manifest.get("unresolved") or []:
                gap = self.gap_for(str(line))
                if gap is not None:
                    found.setdefault((gap.type, gap.name), gap)
        return list(found.values())

    def gap_for(self, unresolved: str) -> SetupGap | None:
        """One installer "unresolved" line as a gap — None when this guide covers it, or when the
        line is not about a source (a reference input, a paid node)."""
        m = _MODEL_GAP.match(unresolved)
        if m:
            node, fld, filename = m.groups()
            if self.covers_model(filename):
                return None
            return SetupGap("model", filename.strip(), _FIELD_KIND.get(fld, ""), f"{node}.{fld}")
        m = _PACK_GAP.match(unresolved) or _ORIGIN_GAP.match(unresolved)
        if m:
            cls = m.group(1).strip()
            return None if self.covers_class(cls) else SetupGap("node_pack", cls, used_by=cls)
        return None

    @staticmethod
    def from_manifests(installer_manifests: list[dict]) -> "TemplateSetupGuide":
        """What the installer lists already source: their models (by destination) and packs (by
        repository). Their gaps stay gaps — `gaps()` names them."""
        packs: dict[str, NodePackSource] = {}
        models: dict[str, ModelSource] = {}
        for manifest in installer_manifests:
            for p in manifest.get("node_packs") or []:
                repo = str(p.get("repository") or "")
                if repo.startswith("https://") and repo not in packs:
                    packs[repo] = NodePackSource(name=str(p.get("directory") or repo.rsplit("/", 1)[-1]),
                                                 repository=repo)
            for m in manifest.get("models") or []:
                dest = str(m.get("destination") or "").replace("\\", "/")
                parts = dest.split("/")
                if len(parts) < 3 or parts[0] != "models" or not str(m.get("url") or "").startswith("https://"):
                    continue
                models.setdefault(dest, ModelSource(filename="/".join(parts[2:]), kind=parts[1], url=str(m["url"])))
        return TemplateSetupGuide(list(packs.values()), list(models.values()))

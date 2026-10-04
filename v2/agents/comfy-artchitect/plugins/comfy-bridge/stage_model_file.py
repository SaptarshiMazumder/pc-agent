"""StageModelFile — a model file a stage loads that the knowledge base does not list, with its source.

A recipe's own files come from the knowledge base, with their links. A design may swap one for
another of the SAME architecture through a port — a Pony, Illustrious or Juggernaut checkpoint
in an SDXL recipe's `checkpoint` — and the file it names then needs its own source: Comfy Cloud
may have it (no `url`), or setup imports it from `url` (civitai.com / huggingface.co) into
`folder`. `base` is what the file IS, in Civitai's words ("Pony", "Illustrious", "SDXL 1.0"): the
LoRA check holds a stage's LoRAs against it, since the file name says nothing a rule can read.
"""

from __future__ import annotations

from dataclasses import dataclass

from model_download_request import ModelDownloadRequest

#: The design tools' `models` parameter (pipeline_plan per stage, stage_set).
MODELS_SCHEMA = {
    "type": "array",
    "description": "Model files the stage loads that kb_lookup does not list — set through a port such as "
                   "`checkpoint` — each {name, folder, base, url}: `name` = the file as the port names it; "
                   "`folder` = checkpoints / diffusion_models / …; `base` = what it is in Civitai's words "
                   "(Pony, Illustrious, SDXL 1.0 — reference_recipe shows it); `url` = its civitai.com / "
                   "huggingface.co download when Comfy Cloud does not have it.",
    "items": {
        "type": "object",
        "required": ["name", "folder"],
        "properties": {
            "name": {"type": "string"},
            "folder": {"type": "string"},
            "base": {"type": "string"},
            "url": {"type": "string"},
        },
    },
}


@dataclass(frozen=True)
class StageModelFile:
    name: str
    folder: str
    base: str = ""
    url: str = ""

    @property
    def file(self) -> str:
        """The bare file name, however the loader spells its folder."""
        return self.name.replace("\\", "/").rsplit("/", 1)[-1]

    @classmethod
    def from_dict(cls, raw) -> "StageModelFile":
        """From the design tools' `models` entries; ValueError naming what is wrong."""
        if not isinstance(raw, dict):
            raise ValueError(f"a model file is {{name, folder, base, url}}, not {raw!r}")
        name = str(raw.get("name") or "").strip()
        folder = str(raw.get("folder") or "").strip()
        if not name:
            raise ValueError("a model file needs `name`: the file as the stage's port names it")
        if ModelDownloadRequest.folder_of(folder) is None:
            raise ValueError(f"model file {name}: '{folder}' is not a ComfyUI model folder "
                             "(checkpoints, diffusion_models, loras, vae, …)")
        return cls(name=name, folder=ModelDownloadRequest.folder_of(folder), base=str(raw.get("base") or "").strip(),
                   url=str(raw.get("url") or "").strip())

    @classmethod
    def list_from(cls, raw) -> list["StageModelFile"]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ValueError(f"`models` is a list of {{name, folder, base, url}}, not {raw!r}")
        return [cls.from_dict(item) for item in raw]

    def to_dict(self) -> dict:
        out = {"name": self.name, "folder": self.folder}
        for key in ("base", "url"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        return out


__all__ = ["MODELS_SCHEMA", "StageModelFile"]

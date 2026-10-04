"""ImageRecipe — how a reference image was made, in the terms a design sets.

Whatever the image carried — a ComfyUI graph, A1111 text, Civitai's record — it comes down to the
same few things: the model, its LoRAs (with strength, and whatever identifies them on Civitai), the
prompts, and the sampling settings and size. ImageRecipeParser fills it; reference_recipe resolves
its LoRAs and says how to recreate it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImageRecipeLora:
    name: str  # as the image names it: a file, an `<lora:…>` name, or a Civitai resource
    strength: float = 1.0
    version_id: int | None = None  # Civitai model version, when the record says
    hash: str = ""  # AutoV2/AutoV3/SHA256 — Civitai finds the version by it


@dataclass
class ImageRecipe:
    source: str  # where the recipe was read: "embedded ComfyUI graph", "A1111 parameters", …
    models: list[str] = field(default_factory=list)
    model_version_ids: list[int] = field(default_factory=list)
    loras: list[ImageRecipeLora] = field(default_factory=list)
    prompt: str = ""
    negative: str = ""
    steps: int | None = None
    cfg: float | None = None
    guidance: float | None = None
    sampler: str = ""
    scheduler: str = ""
    seed: int | None = None
    denoise: float | None = None
    width: int | None = None
    height: int | None = None
    classes: list[str] = field(default_factory=list)  # every node class of an embedded graph
    graph: dict | None = None

    @property
    def empty(self) -> bool:
        return not (self.prompt or self.models or self.loras or self.graph)

    def settings(self) -> dict:
        """The sampling settings the record gives, by the names a recipe's ports use."""
        out = {"steps": self.steps, "cfg": self.cfg, "guidance": self.guidance, "sampler": self.sampler,
               "scheduler": self.scheduler, "seed": self.seed, "denoise": self.denoise,
               "width": self.width, "height": self.height}
        return {k: v for k, v in out.items() if v not in (None, "")}


__all__ = ["ImageRecipe", "ImageRecipeLora"]

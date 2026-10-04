"""StageLora — one LoRA a stage applies on top of its recipe's model.

A LoRA is a design choice like a port value: the file (as ComfyUI's loras/ folder names it), its
strength, and what it is for. `base` is the base model it was trained on, in Civitai's words
("Flux.1 D", "ZImageTurbo", "Wan Video 2.2 I2V-A14B") — ComfyUI applies a LoRA trained for another
model without a word of complaint and the style simply does not appear, so the design checks hold
the base against the stage's model. `url` is where it comes from when Comfy Cloud does not have it
(a civitai.com or huggingface.co download link). `expert` is for two-expert models (Wan 2.2 14B):
'high' goes on the high-noise model, 'low' on the low-noise one. `trigger` is the word or phrase the
LoRA was trained on — the prompt has to carry it, or the effect is weak.
"""

from __future__ import annotations

from dataclasses import dataclass

EXPERTS = ("", "high", "low")

#: The design tools' `loras` parameter (pipeline_plan per stage, stage_set).
LORAS_SCHEMA = {
    "type": "array",
    "description": "LoRAs on the stage's model, in order: [{name, strength, base, url, expert, trigger}]. "
                   "`name` = the file in ComfyUI's loras folder (a Comfy Cloud LoRA from lora_search, or the "
                   "file name a `url` download gives); `base` = what it was trained on, Civitai's words "
                   "(lora_search shows it); `url` = civitai.com / huggingface.co download when Comfy Cloud "
                   "lacks it; `expert` = 'high' | 'low' on a two-expert model (Wan 2.2 14B: give the pair); "
                   "`trigger` = its trained word, which the prompt must carry.",
    "items": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "strength": {"type": "number"},
            "base": {"type": "string"},
            "url": {"type": "string"},
            "expert": {"type": "string", "enum": ["high", "low"]},
            "trigger": {"type": "string"},
        },
    },
}


@dataclass(frozen=True)
class StageLora:
    name: str
    strength: float = 1.0
    base: str = ""
    url: str = ""
    expert: str = ""
    trigger: str = ""

    @classmethod
    def from_dict(cls, raw) -> "StageLora":
        """From the design tools' `loras` entries; ValueError naming what is wrong."""
        if not isinstance(raw, dict):
            raise ValueError(f"a LoRA is {{name, strength, base, url, expert, trigger}}, not {raw!r}")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError("a LoRA needs `name`: the file as ComfyUI's loras folder lists it")
        try:
            strength = float(raw.get("strength", 1.0))
        except (TypeError, ValueError) as e:
            raise ValueError(f"LoRA {name}: strength must be a number") from e
        expert = str(raw.get("expert") or "").strip().lower()
        if expert not in EXPERTS:
            raise ValueError(f"LoRA {name}: expert is 'high' or 'low' (two-expert models only), not {expert!r}")
        return cls(name=name, strength=strength, base=str(raw.get("base") or "").strip(),
                   url=str(raw.get("url") or "").strip(), expert=expert,
                   trigger=str(raw.get("trigger") or "").strip())

    @classmethod
    def list_from(cls, raw) -> list["StageLora"]:
        """A `loras` parameter; ValueError naming what is wrong."""
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ValueError(f"`loras` is a list of {{name, strength, base, url, expert, trigger}}, not {raw!r}")
        return [cls.from_dict(item) for item in raw]

    def to_dict(self) -> dict:
        out = {"name": self.name, "strength": self.strength}
        for key in ("base", "url", "expert", "trigger"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        return out


__all__ = ["EXPERTS", "LORAS_SCHEMA", "StageLora"]

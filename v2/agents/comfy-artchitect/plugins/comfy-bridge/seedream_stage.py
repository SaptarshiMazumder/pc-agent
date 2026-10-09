"""SeedreamStage — a pipeline stage that makes pictures on Seedream 5 Pro, not in ComfyUI.

    {"name": "keyframes", "family": "seedream", "recipe": "seedream-5-pro",
     "ports": {"prompt": "...", "aspect_ratio": "9:16", "count": 2},
     "inputs": {"reference_1": "user:photo", "reference_2": "stage:sheet.image"}}

It has no graph: the run sends its prompt and reference pictures to the provider
(SeedreamImageService). Everything else about a stage holds — its inputs are slots the person
fills or earlier stages' results, it is run by the person's Run click, its results are picked and
handed on. Its one output is `image` (IMAGE). The references go in the order they are bound, so
the prompt can say "the woman in image 1, wearing the jacket from image 2".

This is the contract and its checks only — pure.
"""

from __future__ import annotations

from pipeline import SEEDREAM, Stage

MODEL = "seedream-5-pro"
#: The frames both providers make (fal's size table is the narrower of the two).
ASPECTS = ("9:16", "16:9", "1:1", "4:5", "3:4")
MAX_COUNT = 4
#: fal's edit endpoint takes 10 references (Higgsfield 14): the fallback must be able to run it.
MAX_REFERENCES = 10
OUTPUTS = {"image": {"node": "", "type": "IMAGE"}}
_PORTS = ("prompt", "aspect_ratio", "count")


class SeedreamStage:
    def __init__(self, stage: Stage) -> None:
        if stage.family != SEEDREAM:
            raise ValueError(f"stage {stage.name} is not a Seedream stage")
        self._stage = stage

    @property
    def prompt(self) -> str:
        return str(self._stage.ports.get("prompt") or "").strip()

    @property
    def aspect_ratio(self) -> str:
        return str(self._stage.ports.get("aspect_ratio") or "9:16")

    @property
    def count(self) -> int:
        return int(self._stage.ports.get("count") or 1)

    def problems(self) -> list[str]:
        s, out = self._stage, []
        if s.recipe != MODEL:
            out.append(f"stage {s.name}: a Seedream stage's recipe is '{MODEL}'")
        unknown = sorted(set(s.ports) - set(_PORTS))
        if unknown:
            out.append(f"stage {s.name}: a Seedream stage sets only {', '.join(_PORTS)} (not {', '.join(unknown)})")
        if len(self.prompt.split()) < 5:
            out.append(f"stage {s.name}: write the full prompt — who and what each reference is (image 1, image 2 "
                       "in binding order), the shot, the setting, the light")
        if self.aspect_ratio not in ASPECTS:
            out.append(f"stage {s.name}: aspect_ratio is one of {', '.join(ASPECTS)}")
        count = s.ports.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_COUNT:
            out.append(f"stage {s.name}: count is a whole number from 1 to {MAX_COUNT}")
        if len(s.inputs) > MAX_REFERENCES:
            out.append(f"stage {s.name}: at most {MAX_REFERENCES} reference pictures")
        if s.loras or s.models:
            out.append(f"stage {s.name}: a Seedream stage takes no LoRAs or model files — it is not ComfyUI")
        return out


__all__ = ["ASPECTS", "MAX_COUNT", "MAX_REFERENCES", "MODEL", "OUTPUTS", "SeedreamStage"]

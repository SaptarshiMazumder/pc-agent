"""Every model profile the plugin ships (`model_profiles/*.md`), and what they say about a workflow.

comfy_validate asks this for every graph it checks, with the machine's GPU: each family the graph
runs adds its notes — a better-fitting weight file for this machine, a setting that keeps quality,
the family's own prompt format — and its pitfalls, once. Families are files; nothing here names one.
"""

from __future__ import annotations

from pathlib import Path

from model_profile import ModelProfile

_FOLDER = Path(__file__).parent / "model_profiles"


class ModelProfileCatalog:
    def __init__(self, profiles: list[ModelProfile]) -> None:
        self.profiles = profiles

    @classmethod
    def shipped(cls, folder: Path = _FOLDER) -> "ModelProfileCatalog":
        return cls([ModelProfile.load(p) for p in sorted(Path(folder).glob("*.md"))])

    def notes_for(self, graph: dict, gpu_name: str, vram_gb: float, fixed_design: bool = False) -> str:
        """One block of advice for this graph on this machine; '' when no profile has any. A fixed
        design (template, Library workflow) gets the prompt format only."""
        blocks = []
        for profile in self.profiles:
            notes = profile.notes(graph, gpu_name, vram_gb, fixed_design)
            if not notes:
                continue
            head = (f"{profile.family.upper()} PROFILE (this workflow's models and settings are its own — "
                    "do not change them)" if fixed_design else
                    f"{profile.family.upper()} PROFILE (for this {vram_gb:.0f} GB {gpu_name or 'GPU'}; build "
                    "the new workflow with it)")
            blocks.append(head + ":\n- " + "\n- ".join(notes)
                          + ("\nPitfalls: " + " ".join(profile.pitfalls) if profile.pitfalls else ""))
        return "\n".join(blocks)

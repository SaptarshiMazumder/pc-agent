"""FamilyProfile — what is known about one model family, as the knowledge base records it.

One directory per family under `knowledge_base/` (see knowledge_base/<family>/guide.md for the
prose): `profile.json` holds the facts — every file with its exact name, size, URL and folder; which
recipe fits which task and card; what every parameter does; the RULES that judge a workflow;
prompting; pitfalls; and the source of each fact. This class reads it and answers the questions the
validators and the catalogue ask; it judges nothing itself.

A broken profile is said, not skipped: a profile that cannot be read raises, and the catalogue
refuses to load without it rather than validating with a family silently missing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PROFILE = "profile.json"
GUIDE = "guide.md"


@dataclass
class FamilyProfile:
    id: str
    name: str
    folder: Path
    license: dict
    files: dict[str, dict]  # file name -> its record
    rules: list[dict]
    selection: list[dict]
    prompting: dict
    raw: dict = field(repr=False)

    @classmethod
    def load(cls, folder: Path) -> "FamilyProfile":
        path = Path(folder) / PROFILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"{path}: {e}") from e
        if not data.get("id") or not isinstance(data.get("rules"), list):
            raise ValueError(f"{path}: a family profile needs `id` and `rules`")
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            folder=Path(folder),
            license=dict(data.get("license") or {}),
            files={str(f["name"]): f for f in data.get("files") or [] if isinstance(f, dict) and f.get("name")},
            rules=[r for r in data["rules"] if isinstance(r, dict)],
            selection=[s for s in data.get("selection") or [] if isinstance(s, dict)],
            prompting=dict(data.get("prompting") or {}),
            raw=data,
        )

    def uses(self, file_names: set[str]) -> bool:
        """Does a workflow naming these strings run this family? Only the family's OWN files count:
        a text encoder, VAE or face detector several families share (clip_l, ae.safetensors) says
        nothing about which family it is, and counting it put SD3.5's rules on an SDXL graph."""
        return any(_base(n) in self._signal_bases for n in file_names)

    def file(self, name: str) -> dict | None:
        return self.files.get(name) or next((f for n, f in self.files.items() if _base(n) == _base(name)), None)

    def guide(self) -> str:
        p = self.folder / GUIDE
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def prompting_guide(self) -> str:
        """The guide's prompting part: from its first heading that names prompts to the next
        heading of the same level. The whole guide when it has no such heading."""
        text = self.guide()
        lines = text.splitlines()
        start = next((i for i, l in enumerate(lines) if l.startswith("#") and "prompt" in l.lower()), None)
        if start is None:
            return text
        level = len(lines[start]) - len(lines[start].lstrip("#"))
        end = next((j for j in range(start + 1, len(lines))
                    if lines[j].startswith("#") and len(lines[j]) - len(lines[j].lstrip("#")) <= level), len(lines))
        return "\n".join(lines[start:end]).strip()

    def dotted(self, path: str):
        """A value in the profile by dotted path ("prompting.required_sections.base")."""
        cur = self.raw
        for part in str(path).split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    @property
    def _signal_bases(self) -> set[str]:
        return {_base(n) for n, f in self.files.items() if str(f.get("role") or "") not in SHARED_ROLES}


#: File roles that do not identify a family: companions several families load.
SHARED_ROLES = frozenset({
    "text_encoder", "vae", "clip_vision", "insightface", "facexlib", "audio_encoder", "audio_vae",
    "pose_extractor", "preprocessor", "metadata", "projection", "geometry_estimation", "decoder",
})


def _base(value: str) -> str:
    return str(value).replace("\\", "/").rsplit("/", 1)[-1].lower()


__all__ = ["FamilyProfile"]

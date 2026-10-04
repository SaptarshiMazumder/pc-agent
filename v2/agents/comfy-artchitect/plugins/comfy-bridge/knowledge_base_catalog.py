"""KnowledgeBaseCatalog — every model family the agent knows, and their recipes.

`knowledge_base/` ships with the plugin: one folder per family (FamilyProfile), each with its
recipes (Recipe). This loads them all and answers the questions that need more than one family:
which families a workflow runs (by the model files it names), where a file is described, which
recipe a family offers for a task.

Families are folders; nothing here names one. Adding a family is adding a folder.

ONE BROKEN FAMILY IS ONE FAMILY GONE, NOT ALL OF THEM. A folder whose profile or recipe cannot be
read is left out and named in `broken` — the design tools say so in every report — while every
other family works. Refusing the whole catalogue over one folder (a family half-written in place)
took down every design tool in a live chat, and the agent reported itself blocked.
"""

from __future__ import annotations

from pathlib import Path

from api_graph import ApiGraph
from family_profile import FamilyProfile
from model_download_request import ModelDownloadRequest
from recipe import SUFFIX, Recipe

FOLDER = Path(__file__).parent / "knowledge_base"


class KnowledgeBaseCatalog:
    def __init__(self, families: list[FamilyProfile], recipes: list[Recipe],
                 broken: dict[str, str] | None = None) -> None:
        self.families = {f.id: f for f in families}
        self.recipes = {(r.family, r.id): r for r in recipes}
        #: folder name -> why it could not be read (left out of the catalogue)
        self.broken = dict(broken or {})

    @classmethod
    def shipped(cls, folder: Path = FOLDER) -> "KnowledgeBaseCatalog":
        families, recipes, broken = [], [], {}
        for d in sorted(p for p in Path(folder).iterdir() if p.is_dir()):
            try:
                profile = FamilyProfile.load(d)
                mine = []
                for rp in sorted((d / "recipes").glob(f"*{SUFFIX}")):
                    r = Recipe.load(rp)
                    r.family = r.family or profile.id
                    mine.append(r)
            except (OSError, ValueError) as e:
                broken[d.name] = str(e)
                continue
            families.append(profile)
            recipes += mine
        return cls(families, recipes, broken)

    def families_for(self, graph: dict) -> list[FamilyProfile]:
        """The families whose model files this workflow names, in a stable order."""
        names = ApiGraph(graph).strings()
        return [f for f in self.families.values() if f.uses(names)]

    def describe_file(self, name: str) -> tuple[FamilyProfile, dict] | None:
        for f in self.families.values():
            rec = f.file(name)
            if rec is not None:
                return f, rec
        return None

    def file_bytes(self, name: str) -> int | None:
        """A model file's download size: its exact byte count, else the profile's `size_gb` (decimal
        GB, as the Hugging Face API reports it). None only when the knowledge base gives neither."""
        found = self.describe_file(name)
        if found is None:
            return None
        rec = found[1]
        if rec.get("bytes"):
            return int(rec["bytes"])
        if rec.get("size_gb"):
            return int(float(rec["size_gb"]) * 1e9)
        return None

    def model_sources(self) -> dict[str, dict]:
        """{file name: {url, folder}} for every file the knowledge base links and files under a
        folder ComfyUI has — where a portable installer fetches a model nothing else names."""
        out: dict[str, dict] = {}
        for fam in self.families.values():
            for name, rec in fam.files.items():
                url = str(rec.get("url") or "")
                folder = ModelDownloadRequest.folder_of(str(rec.get("folder") or ""))
                if name and url.startswith("https://") and folder and name not in out:
                    out[name] = {"url": url, "folder": folder}
        return out

    def recipe(self, family: str, recipe_id: str) -> Recipe | None:
        return self.recipes.get((family, recipe_id))

    def recipes_of(self, family: str, task: str = "") -> list[Recipe]:
        return [r for (fam, _), r in self.recipes.items() if fam == family and (not task or r.task == task)]

    def unknown_model_files(self, graph: dict) -> list[str]:
        """Model files the graph names that no family describes — a design outside the knowledge
        base, whose wiring nothing here can vouch for."""
        exts = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".sft", ".onnx")
        return sorted(n for n in ApiGraph(graph).strings()
                      if n.lower().endswith(exts) and self.describe_file(n) is None)


__all__ = ["KnowledgeBaseCatalog"]

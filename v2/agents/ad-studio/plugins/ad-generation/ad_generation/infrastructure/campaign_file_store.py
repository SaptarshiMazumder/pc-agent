"""A campaign as a folder in the workspace:

    campaigns/<id>/product/          copies of the product photos
    campaigns/<id>/profile.json      what the product is
    campaigns/<id>/brief.json        the look and each scene's prompts
    campaigns/<id>/steps.json        the campaign's checklist (its steps, picks, last settings)
    campaigns/<id>/steps/<step>/     take-01-1.png, take-02.mp4 (+ take-02-last.png), ...
    campaigns/<id>/ledger.jsonl      one line per paid result, tagged with its step
    campaigns/<id>/verdicts.jsonl    one line per check

Campaigns from before checklists keep their files where they were (stills/<shot>/,
clips/<shot>/, sheet/) and their gate-era progress.json, read once into a checklist.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil

from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.creative_brief import CreativeBrief
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.media_verdict import MediaVerdict
from ad_generation.domain.product_profile import ProductProfile
from ad_generation.infrastructure.run_workspace import RunWorkspace

ROOT = "campaigns"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "product"


class CampaignFileStore:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def create(self, name: str, photos: list[str]) -> tuple[str, tuple[str, ...]]:
        base = _slug(name)
        n = 1
        while self._ws.path(f"{ROOT}/{base}-{n:02d}").exists():
            n += 1
        campaign_id = f"{base}-{n:02d}"
        copies = []
        seen = set()
        for photo in photos:
            src = self._ws.path(photo)
            if not src.is_file():
                raise FileNotFoundError(f"no product photo at {photo}")
            # The same picture twice (an upload repeated across chats) is ONE reference: a copy
            # adds nothing for the image model and doubles what every check has to read.
            digest = hashlib.sha1(src.read_bytes()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            rel = f"{ROOT}/{campaign_id}/product/photo-{len(copies) + 1}{src.suffix.lower()}"
            dest = self._ws.path(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            copies.append(rel)
        return campaign_id, tuple(copies)

    def save_profile(self, campaign_id: str, profile: ProductProfile) -> None:
        self._write(campaign_id, "profile.json", profile.to_dict())

    def profile(self, campaign_id: str) -> ProductProfile:
        return ProductProfile.load(self._read(campaign_id, "profile.json", "a product profile"))

    def save_brief(self, campaign_id: str, brief: CreativeBrief) -> None:
        self._write(campaign_id, "brief.json", brief.to_dict())

    def brief(self, campaign_id: str) -> CreativeBrief:
        return CreativeBrief.from_dict(self._read(campaign_id, "brief.json", "a brief (run its brief step)"))

    def save_checklist(self, campaign_id: str, checklist: CampaignChecklist) -> None:
        self._write(campaign_id, "steps.json", checklist.to_dict())

    def checklist(self, campaign_id: str) -> CampaignChecklist | None:
        path = self._dir(campaign_id) / "steps.json"
        if not path.is_file():
            return None
        return CampaignChecklist.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def legacy_progress(self, campaign_id: str) -> dict | None:
        path = self._dir(campaign_id) / "progress.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def campaign_ids(self) -> list[str]:
        root = self._ws.path(ROOT)
        if not root.is_dir():
            return []
        return sorted(
            p.name for p in root.iterdir() if (p / "steps.json").is_file() or (p / "progress.json").is_file()
        )

    def updated(self, campaign_id: str) -> float:
        return max(p.stat().st_mtime for p in self._dir(campaign_id).iterdir())

    def take_stem(self, campaign_id: str, step_id: str) -> str:
        folder = self._dir(campaign_id) / "steps" / step_id
        folder.mkdir(parents=True, exist_ok=True)
        n = 1
        while any(folder.glob(f"take-{n:02d}*")):
            n += 1
        return f"{ROOT}/{campaign_id}/steps/{step_id}/take-{n:02d}"

    def import_file(self, campaign_id: str, step_id: str, src: str) -> str:
        source = self._ws.path(src)
        if not source.is_file():
            raise FileNotFoundError(f"no file at {src}")
        rel = f"{self.take_stem(campaign_id, step_id)}-1{source.suffix.lower()}"
        shutil.copyfile(source, self._ws.path(rel))
        return rel

    def record(self, campaign_id: str, media: GeneratedMedia) -> None:
        self._append(campaign_id, "ledger.jsonl", media.to_dict())

    def record_verdict(self, campaign_id: str, verdict: MediaVerdict) -> None:
        self._append(campaign_id, "verdicts.jsonl", verdict.to_dict())

    def verdicts(self, campaign_id: str) -> dict[str, MediaVerdict]:
        path = self._dir(campaign_id) / "verdicts.jsonl"
        if not path.is_file():
            return {}
        out: dict[str, MediaVerdict] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                v = MediaVerdict.load(json.loads(line))
                out[v.path] = v
        return out

    def ledger(self, campaign_id: str) -> list[GeneratedMedia]:
        path = self._dir(campaign_id) / "ledger.jsonl"
        if not path.is_file():
            return []
        return [
            GeneratedMedia.from_dict(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def made_at(self, path: str) -> float:
        p = self._ws.path(path)
        return p.stat().st_mtime if p.is_file() else 0.0

    def spent(self, campaign_id: str) -> float:
        return sum(m.cost_usd for m in self.ledger(campaign_id))

    # --- files ---------------------------------------------------------------------------------

    def _dir(self, campaign_id: str):
        path = self._ws.path(f"{ROOT}/{campaign_id}")
        if not path.is_dir():
            raise KeyError(f"no campaign '{campaign_id}'")
        return path

    def _write(self, campaign_id: str, name: str, data: dict) -> None:
        (self._dir(campaign_id) / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _read(self, campaign_id: str, name: str, what: str) -> dict:
        path = self._dir(campaign_id) / name
        if not path.is_file():
            raise KeyError(f"campaign '{campaign_id}' has no {what} yet")
        return json.loads(path.read_text(encoding="utf-8"))

    def _append(self, campaign_id: str, name: str, data: dict) -> None:
        with open(self._dir(campaign_id) / name, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

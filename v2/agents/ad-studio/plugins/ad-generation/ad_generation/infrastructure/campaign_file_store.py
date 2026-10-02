"""A campaign as a folder in the workspace:

    campaigns/<id>/product/        copies of the product photos
    campaigns/<id>/profile.json    what the product is
    campaigns/<id>/brief.json      the ad's plan
    campaigns/<id>/progress.json   which gate a campaign_run waits at, and each shot so far
    campaigns/<id>/sheet/          take-01-1.png — the shoot sheet (recipes with shoot_sheet)
    campaigns/<id>/stills/<shot>/  take-01-1.jpg, take-01-2.jpg, take-02-1.jpg, ...
    campaigns/<id>/clips/<shot>/   take-01.mp4 (+ take-01-last.png)
    campaigns/<id>/ledger.jsonl    one line per paid result
    campaigns/<id>/verdicts.jsonl  one line per check
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil

from ad_generation.domain.campaign_progress import CampaignProgress
from ad_generation.domain.creative_brief import CreativeBrief
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.media_verdict import MediaVerdict
from ad_generation.domain.product_profile import ProductProfile
from ad_generation.infrastructure.run_workspace import RunWorkspace

ROOT = "campaigns"
_TAKE = re.compile(r"^take-(\d+)")
_CLIP_SUFFIXES = (".mp4", ".mov", ".webm")


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
        return ProductProfile.load(self._read(campaign_id, "profile.json", "a product profile (run product_analyze)"))

    def save_brief(self, campaign_id: str, brief: CreativeBrief) -> None:
        self._write(campaign_id, "brief.json", brief.to_dict())

    def brief(self, campaign_id: str) -> CreativeBrief:
        return CreativeBrief.from_dict(self._read(campaign_id, "brief.json", "a brief (run campaign_brief)"))

    def save_progress(self, campaign_id: str, progress: CampaignProgress) -> None:
        self._write(campaign_id, "progress.json", progress.to_dict())

    def campaign_ids(self) -> list[str]:
        root = self._ws.path(ROOT)
        if not root.is_dir():
            return []
        return sorted(p.parent.name for p in root.glob("*/progress.json"))

    def updated(self, campaign_id: str) -> float:
        return max(p.stat().st_mtime for p in self._dir(campaign_id).iterdir())

    def progress(self, campaign_id: str) -> CampaignProgress:
        return CampaignProgress.from_dict(
            self._read(campaign_id, "progress.json", "recipe progress (start it with campaign_run)")
        )

    def sheet_stem(self, campaign_id: str) -> str:
        folder = self._dir(campaign_id) / "sheet"
        folder.mkdir(parents=True, exist_ok=True)
        n = 1
        while any(folder.glob(f"take-{n:02d}*")):
            n += 1
        return f"{ROOT}/{campaign_id}/sheet/take-{n:02d}"

    def still_stem(self, campaign_id: str, shot_id: str) -> str:
        return self._next_take(campaign_id, "stills", shot_id)

    def clip_path(self, campaign_id: str, shot_id: str) -> str:
        return self._next_take(campaign_id, "clips", shot_id) + ".mp4"

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

    def stills_made(self, campaign_id: str, shot_id: str) -> list[list[str]]:
        return [[self._ws.rel(p) for p in take] for take in self._takes(campaign_id, "stills", shot_id)]

    def clips_made(self, campaign_id: str, shot_id: str) -> list[tuple[str, str]]:
        out = []
        for take in self._takes(campaign_id, "clips", shot_id):
            clip = next((p for p in take if p.suffix.lower() in _CLIP_SUFFIXES), None)
            if clip is None:
                continue
            last = next((p for p in take if p.stem.endswith("-last")), None)
            out.append((self._ws.rel(clip), self._ws.rel(last) if last else ""))
        return out

    def spent(self, campaign_id: str) -> float:
        path = self._dir(campaign_id) / "ledger.jsonl"
        if not path.is_file():
            return 0.0
        return sum(json.loads(line)["cost_usd"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

    # --- files ---------------------------------------------------------------------------------

    def _dir(self, campaign_id: str):
        path = self._ws.path(f"{ROOT}/{campaign_id}")
        if not path.is_dir():
            raise KeyError(f"no campaign '{campaign_id}'")
        return path

    def _takes(self, campaign_id: str, kind: str, shot_id: str) -> list[list]:
        """The takes on disk, grouped by number, oldest first — skipping files older than the
        current brief: they were made for another brief and must not stand in for this one."""
        folder = self._dir(campaign_id) / kind / shot_id
        if not folder.is_dir():
            return []
        since = (self._dir(campaign_id) / "brief.json").stat().st_mtime
        takes: dict[int, list] = {}
        for p in sorted(folder.iterdir()):
            m = _TAKE.match(p.name)
            if m and p.is_file() and p.stat().st_mtime >= since:
                takes.setdefault(int(m.group(1)), []).append(p)
        return [takes[n] for n in sorted(takes)]

    def _next_take(self, campaign_id: str, kind: str, shot_id: str) -> str:
        folder = self._dir(campaign_id) / kind / shot_id
        folder.mkdir(parents=True, exist_ok=True)
        n = 1
        while any(folder.glob(f"take-{n:02d}*")):
            n += 1
        return f"{ROOT}/{campaign_id}/{kind}/{shot_id}/take-{n:02d}"

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

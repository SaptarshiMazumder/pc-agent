"""A campaign from before checklists — its gate-era progress.json — as a checklist, so it opens and
continues like any other. Its files stay where they were (stills/<shot>/, clips/<shot>/, sheet/);
`owner_of` places them in their steps.

The recipe's steps are the checklist; what the old campaign had chosen becomes the picks, what it
had made marks the steps done, and its model choices become the steps' last settings.
"""

from __future__ import annotations

from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.recipe import Recipe

_PAST_STILLS = ("clips", "done")


def checklist_from_progress(data: dict, recipe: Recipe) -> CampaignChecklist:
    checklist = recipe.new_checklist(
        str(data.get("cast_name") or ""), dict(data.get("direction") or {}), str(data.get("session") or "")
    )
    plan = data.get("plan") or {}
    checklist.budget_usd = float(plan.get("budget_usd") or checklist.budget_usd)
    gate = str(data.get("gate") or "brief")
    outcomes = data.get("outcomes") or {}
    for step in list(checklist.steps):
        if step.action == "brief":
            checklist.put(step.with_(status="done" if gate != "brief" else "todo"))
        elif step.action == "sheet":
            sheet = str(data.get("sheet") or "")
            checklist.put(step.with_(pick=sheet, status="done" if sheet else "todo", model=str(data.get("sheet_image") or "")))
        elif step.action == "images":
            outcome = outcomes.get(step.scene) or {}
            still = str(outcome.get("still") or "")
            made = still or outcome.get("stills") or outcome.get("rejected")
            checklist.put(
                step.with_(
                    pick=still,
                    status="done" if (made or gate in _PAST_STILLS) else "todo",
                    model=str(data.get("image") or ""),
                )
            )
        elif step.action == "video":
            outcome = outcomes.get(step.scene) or {}
            clip = str(outcome.get("clip") or "")
            checklist.put(
                step.with_(
                    pick=clip,
                    status="done" if clip else "todo",
                    model=str(data.get("video") or ""),
                    seconds=int(data.get("clip_seconds") or 0),
                    resolution=str(plan.get("resolution") or step.resolution),
                )
            )
    return checklist


def owner_of(checklist: CampaignChecklist, path: str, tagged: str) -> str:
    """The step a result belongs to: the one it was tagged with, else — a result from before
    steps — the step its folder stands for (sheet/, stills/<scene>/, clips/<scene>/)."""
    if tagged:
        return tagged
    parts = path.replace("\\", "/").split("/")
    if "steps" in parts:
        i = parts.index("steps")
        return parts[i + 1] if i + 1 < len(parts) else ""
    if "sheet" in parts:
        step = checklist.first("sheet")
        return step.id if step else ""
    for folder, action in (("stills", "images"), ("clips", "video")):
        if folder in parts:
            i = parts.index(folder)
            scene = parts[i + 1] if i + 1 < len(parts) - 1 else ""
            match = next((s for s in checklist.steps if s.action == action and s.scene == scene), None)
            step = match or checklist.first(action)
            return step.id if step else ""
    return ""

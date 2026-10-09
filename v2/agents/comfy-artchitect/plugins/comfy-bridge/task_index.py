"""TaskIndex — which model family is BEST for a kind of job, across families.

Each family profile says which of its own recipes suits a task; nothing there compares families.
`knowledge_base/task_index.json` does: per task (t2i-photoreal, edit-identity, i2v, …) an ordered
list, best first, each entry with why, its evidence (leaderboard, official claim, community — with
source and date) and the constraints that can rule it out for a job (gated download, VRAM). The agent picks from the top down; the user never has to name a model.

Absent file = no cross-family ranking: lookups then list each family's own picks unranked, and say
so — never an order invented on the spot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

FILE = "task_index.json"


@dataclass(frozen=True)
class RankedChoice:
    rank: int
    family: str
    recipe: str  # the exact recipe id to start from; "" when the family has none for this yet
    recipe_hint: str  # the variant as the evidence names it
    why: str
    evidence: list
    constraints: list
    #: A ROUTE, when the best answer is several stages, not one model: [{family, recipe, does}] in
    #: order (a scene rendered at the exact size, then reference edits into it). Empty for a model.
    route: tuple = ()


@dataclass(frozen=True)
class TaskDescription:
    #: What the task is FOR, in the words a brief uses — what a search by words matches.
    gloss: str
    #: The recipe `task` values (t2i, edit, r2v, …) that serve it.
    recipe_tasks: tuple
    #: What serves it BEFORE any recipe, when that is decided (a realistic still is a Seedream
    #: stage) — said above the ranking; "" when the ranking decides.
    first_choice: str = ""


class TaskIndex:
    """THE TASK LIST IS DATA. Each task's description and recipe kinds live in the file next to its
    ranking (`task_meta`): a task added there is searchable and askable by name — a list kept in code
    hid a new task from every lookup until someone remembered to copy it over."""

    def __init__(self, tasks: dict[str, list[RankedChoice]], meta: dict,
                 described: dict[str, TaskDescription] | None = None) -> None:
        self.tasks = tasks
        self.meta = meta
        self.described = described or {}

    @classmethod
    def load(cls, folder: Path) -> "TaskIndex | None":
        p = Path(folder) / FILE
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        tasks = {}
        for task, rows in (data.get("tasks") or {}).items():
            tasks[str(task)] = [
                RankedChoice(i + 1, str(r.get("family") or ""), str(r.get("recipe") or ""),
                             str(r.get("recipe_hint") or ""), str(r.get("why") or ""),
                             list(r.get("evidence") or []), list(r.get("constraints") or []),
                             tuple(s for s in r.get("route") or [] if isinstance(s, dict)))
                for i, r in enumerate(rows) if isinstance(r, dict) and r.get("family")
            ]
        described = {
            str(task): TaskDescription(str(m.get("gloss") or ""), tuple(str(t) for t in m.get("recipe_tasks") or ()),
                                       str(m.get("first_choice") or ""))
            for task, m in (data.get("task_meta") or {}).items() if isinstance(m, dict)
        }
        return cls(tasks, {k: v for k, v in data.items() if k not in ("tasks", "task_meta")}, described)

    def ranked(self, task: str) -> list[RankedChoice]:
        return list(self.tasks.get(task) or [])

    def gloss(self, task: str) -> str:
        d = self.described.get(task)
        return d.gloss if d else ""

    def first_choice(self, task: str) -> str:
        d = self.described.get(task)
        return d.first_choice if d else ""

    def recipe_tasks(self, task: str) -> tuple:
        """The recipe kinds that serve `task`; a task the file does not describe is its own kind."""
        d = self.described.get(task)
        return d.recipe_tasks if d and d.recipe_tasks else (task,)

    @property
    def task_ids(self) -> list[str]:
        return sorted(set(self.tasks) | set(self.described))


__all__ = ["FILE", "RankedChoice", "TaskDescription", "TaskIndex"]

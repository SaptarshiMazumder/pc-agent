"""TaskIndex — which model family is BEST for a kind of job, across families.

Each family profile says which of its own recipes suits a task; nothing there compares families.
`knowledge_base/task_index.json` does: per task (t2i-photoreal, edit-identity, i2v, …) an ordered
list, best first, each entry with why, its evidence (leaderboard, official claim, community — with
source and date) and the constraints that can rule it out for a job (licence, gated download,
VRAM). The agent picks from the top down; the user never has to name a model.

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


class TaskIndex:
    def __init__(self, tasks: dict[str, list[RankedChoice]], meta: dict) -> None:
        self.tasks = tasks
        self.meta = meta

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
                             list(r.get("evidence") or []), list(r.get("constraints") or []))
                for i, r in enumerate(rows) if isinstance(r, dict) and r.get("family")
            ]
        return cls(tasks, {k: v for k, v in data.items() if k != "tasks"})

    def ranked(self, task: str) -> list[RankedChoice]:
        return list(self.tasks.get(task) or [])

    @property
    def task_ids(self) -> list[str]:
        return sorted(self.tasks)


__all__ = ["FILE", "RankedChoice", "TaskIndex"]

"""ProjectManager — the port for the manager's judgement.

Two questions, both answered from a ManagerBrief (the lean, independent view — never the
developer's transcript):

  * draft_contract: what does "done" mean for what the user asked? Also re-asked when the user
    replies to a contract awaiting approval: the answer comes back ACTIVE if they approved it
    (possibly amended), still PENDING_APPROVAL if they asked for changes that need a fresh yes.
  * review: given what ran and what the developer claims, what happens next?

An implementation that cannot answer raises ManagerUnavailable. The caller surfaces it; it never
silently passes a checkpoint as if the manager had approved.
"""

from __future__ import annotations

from typing import Protocol

from agent_runtime.domain.deliverable_contract import DeliverableContract
from agent_runtime.domain.manager_brief import ManagerBrief
from agent_runtime.domain.manager_verdict import ManagerVerdict


class ManagerUnavailable(Exception):
    """The manager could not reach a decision (model error, unparseable answer)."""


class ProjectManager(Protocol):
    async def draft_contract(self, brief: ManagerBrief) -> DeliverableContract: ...

    async def review(self, brief: ManagerBrief) -> ManagerVerdict: ...


__all__ = ["ManagerUnavailable", "ProjectManager"]

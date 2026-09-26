"""DeliverableContract — what "done" means for a piece of work, agreed before it starts.

The manager drafts it from the user's own words; when the work creates or changes something
lasting (an agent, infrastructure) or spends money, the user approves it first. From then on it
is the yardstick: the developer cannot end the work while a criterion is unproven, and cannot edit
the criteria — only the user can change what was asked for.

EVERY CRITERION CARRIES A PROOF — something that can be checked without taking anyone's word:

    artifact_produced   {"glob": "workspace-relative glob"}           a file matching it exists
    file_contains       {"path": "...", "text": "..."}                a file holds that text
    command_succeeds    {"command": "...", "expect": "optional text"} exits 0 (and prints expect)
    scenario_passes     {"scenario_path": "..."}                      an e2e scenario passes
    judged              {}                                            the manager judges it from
                                                                      the evidence it is shown

"Works well" is not a criterion. "The plan output contains aws_s3_bucket" is.

WHAT THE USER SEES is `present()`, and only that: a few plain bullets of what they will get, what
is needed from them, and how to reply. Never the proofs, never files or templates — the proof is
the manager's business, the plan is theirs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

ARTIFACT_PRODUCED = "artifact_produced"
FILE_CONTAINS = "file_contains"
COMMAND_SUCCEEDS = "command_succeeds"
SCENARIO_PASSES = "scenario_passes"
JUDGED = "judged"

PROOF_KINDS = (ARTIFACT_PRODUCED, FILE_CONTAINS, COMMAND_SUCCEEDS, SCENARIO_PASSES, JUDGED)

PENDING_APPROVAL = "pending_approval"
ACTIVE = "active"
FULFILLED = "fulfilled"

CONTRACT_STATUSES = (PENDING_APPROVAL, ACTIVE, FULFILLED)


@dataclass(frozen=True)
class Proof:
    kind: str
    spec: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in PROOF_KINDS:
            raise ValueError(f"unknown proof kind {self.kind!r} (expected one of {PROOF_KINDS})")


@dataclass(frozen=True)
class Criterion:
    id: str
    statement: str
    proof: Proof


@dataclass(frozen=True)
class DeliverableContract:
    goal: str
    criteria: tuple[Criterion, ...]
    needs_approval: bool
    status: str
    needs_from_user: tuple[str, ...] = ()  # what only the user can provide, one line each

    def __post_init__(self) -> None:
        if self.status not in CONTRACT_STATUSES:
            raise ValueError(f"unknown contract status {self.status!r}")
        if not self.criteria:
            raise ValueError("a contract needs at least one criterion")

    @property
    def active(self) -> bool:
        return self.status == ACTIVE

    @property
    def pending_approval(self) -> bool:
        return self.status == PENDING_APPROVAL

    def fulfilled(self) -> "DeliverableContract":
        return replace(self, status=FULFILLED)

    def criterion(self, criterion_id: str) -> Criterion | None:
        return next((c for c in self.criteria if c.id == criterion_id), None)

    def present(self, revised: bool = False) -> str:
        """The approval message the user sees — fixed shape, no essays, nothing internal."""
        lines = ["**Revised plan**" if revised else "**Plan**"]
        lines += [f"- {c.statement}" for c in self.criteria]
        if self.needs_from_user:
            lines += ["", "**Need from you**"] + [f"- {n}" for n in self.needs_from_user]
        lines += [
            "",
            "Reply **Approve** to start, or **Change it** and say what to change.",
            "",
            "```suggest",
            "Approve | Approve this plan and start",
            "Change it | Tell me what to change",
            "```",
        ]
        return "\n".join(lines)

    def render(self) -> str:
        lines = [f"Goal: {self.goal}", f"Status: {self.status}"]
        for c in self.criteria:
            lines.append(f"  [{c.id}] {c.statement}  (proof: {c.proof.kind} {c.proof.spec or ''})".rstrip())
        return "\n".join(lines)

    @classmethod
    def from_dict(cls, d: dict) -> "DeliverableContract":
        criteria = tuple(
            Criterion(
                id=str(c.get("id") or f"c{i + 1}"),
                statement=str(c.get("statement") or "").strip(),
                proof=Proof(
                    kind=str((c.get("proof") or {}).get("kind") or JUDGED),
                    spec={k: v for k, v in (c.get("proof") or {}).items() if k != "kind"},
                ),
            )
            for i, c in enumerate(d.get("criteria") or [])
        )
        return cls(
            goal=str(d.get("goal") or "").strip(),
            criteria=criteria,
            needs_approval=bool(d.get("needs_approval")),
            status=str(d.get("status") or ACTIVE),
            needs_from_user=tuple(str(n).strip() for n in (d.get("needs_from_user") or []) if str(n).strip()),
        )

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "needs_approval": self.needs_approval,
            "status": self.status,
            "needs_from_user": list(self.needs_from_user),
            "criteria": [
                {"id": c.id, "statement": c.statement, "proof": {"kind": c.proof.kind, **c.proof.spec}}
                for c in self.criteria
            ],
        }


__all__ = [
    "ACTIVE",
    "ARTIFACT_PRODUCED",
    "COMMAND_SUCCEEDS",
    "FILE_CONTAINS",
    "FULFILLED",
    "JUDGED",
    "PENDING_APPROVAL",
    "PROOF_KINDS",
    "SCENARIO_PASSES",
    "Criterion",
    "DeliverableContract",
    "Proof",
]

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

DECISIONS ARE PART OF THE CONTRACT. What only the user can decide — how the work connects to an
outside service, with which account — is a question in the contract, and a contract with an
unanswered question is never in force: the work cannot start on a guess. A builder once spent
thirty minutes on an AWS agent whose connection nobody had chosen, and the gap only surfaced at
the end.

WHAT THE USER SEES is `present()`, and only that: a few plain bullets of what they will get, what
they must decide, what is needed from them, and how to reply. Never the proofs, never files or
templates — the proof is the manager's business, the plan is theirs.
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
class Decision:
    id: str
    question: str  # one plain line the user can answer
    answer: str = ""  # the user's answer, verbatim in substance; "" while open

    @property
    def open(self) -> bool:
        return not self.answer


@dataclass(frozen=True)
class DeliverableContract:
    goal: str
    criteria: tuple[Criterion, ...]
    needs_approval: bool
    status: str
    needs_from_user: tuple[str, ...] = ()  # what only the user can provide, one line each
    decisions: tuple[Decision, ...] = ()  # what only the user can decide; all answered before work

    def __post_init__(self) -> None:
        if self.status not in CONTRACT_STATUSES:
            raise ValueError(f"unknown contract status {self.status!r}")
        if not self.criteria:
            raise ValueError("a contract needs at least one criterion")
        if self.status == ACTIVE and self.open_decisions:
            raise ValueError("a contract with an unanswered decision cannot be in force")

    @property
    def open_decisions(self) -> tuple[Decision, ...]:
        return tuple(d for d in self.decisions if d.open)

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

    def plan_points(self) -> str:
        """Just the bullets — for an agent that shows them inside its OWN approval card."""
        lines = [f"- {c.statement}" for c in self.criteria]
        lines += [f"- Decide: {d.question}" for d in self.open_decisions]
        lines += [f"- Needed from you: {n}" for n in self.needs_from_user]
        return "\n".join(lines)

    def present(self, revised: bool = False) -> str:
        """The approval message the user sees — fixed shape, no essays, nothing internal."""
        lines = ["**Revised plan**" if revised else "**Plan**"]
        lines += [f"- {c.statement}" for c in self.criteria]
        if self.open_decisions:
            lines += ["", "**Decide**"] + [f"- {d.question}" for d in self.open_decisions]
        if self.needs_from_user:
            lines += ["", "**Need from you**"] + [f"- {n}" for n in self.needs_from_user]
        reply = (
            "Answer the questions above, then **Approve** to start — or **Change it** and say what to change."
            if self.open_decisions
            else "Reply **Approve** to start, or **Change it** and say what to change."
        )
        lines += [
            "",
            reply,
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
        if self.decisions:
            lines.append("Decisions (the user's; the work must follow each answer):")
            lines += [f"  [{d.id}] {d.question} -> {d.answer or 'OPEN'}" for d in self.decisions]
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
        decisions = tuple(
            Decision(
                id=str(q.get("id") or f"d{i + 1}"),
                question=str(q.get("question") or "").strip(),
                answer=str(q.get("answer") or "").strip(),
            )
            for i, q in enumerate(d.get("decisions") or [])
            if str(q.get("question") or "").strip()
        )
        status = str(d.get("status") or ACTIVE)
        if status == ACTIVE and any(q.open for q in decisions):
            # The work does not start on a guess: an open question holds the contract for the user.
            status = PENDING_APPROVAL
        return cls(
            goal=str(d.get("goal") or "").strip(),
            criteria=criteria,
            needs_approval=bool(d.get("needs_approval")) or status == PENDING_APPROVAL,
            status=status,
            needs_from_user=tuple(str(n).strip() for n in (d.get("needs_from_user") or []) if str(n).strip()),
            decisions=decisions,
        )

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "needs_approval": self.needs_approval,
            "status": self.status,
            "needs_from_user": list(self.needs_from_user),
            "decisions": [{"id": d.id, "question": d.question, "answer": d.answer} for d in self.decisions],
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
    "Decision",
    "DeliverableContract",
    "Proof",
]

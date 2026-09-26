"""The manager reviews a stop once, and never loops on the same facts.

Replays the staging failure of 2026-09-26: a Comfy Penguin run stopped on inputs the user had not
added yet; the manager kept answering "send it back", the developer could only write to the user
again, and the same review of the same facts ran twelve times — twelve near-identical messages.
"""

from __future__ import annotations

import asyncio

from agent_runtime.application.services.manager_checkpoint_service import (
    ManagerCheckpointService,
)
from agent_runtime.application.services.run_digest_recorder import RunDigestRecorder
from agent_runtime.domain.capability_sheet import CapabilitySheet
from agent_runtime.domain.deliverable_contract import ACTIVE, Criterion, DeliverableContract, Proof
from agent_runtime.domain.escalation_policy import EscalationPolicy
from agent_runtime.domain.manager_brief import FINISH
from agent_runtime.domain.manager_ledger import ManagerLedger
from agent_runtime.domain.manager_verdict import REDIRECT, WAIT, ManagerVerdict
from agent_runtime.domain.messages import AssistantMessage, TextContent, UserMessage
from agent_runtime.domain.proof_result import ProofResult

CONTRACT = DeliverableContract(
    goal="a 3-second fight clip",
    criteria=(Criterion("c1", "the fight clip is rendered", Proof("artifact_produced", {"glob": "*.mp4"})),),
    needs_approval=False,
    status=ACTIVE,
)


class _Store:
    def __init__(self) -> None:
        self.ledger = ManagerLedger(contract=CONTRACT)

    def load(self) -> ManagerLedger:
        return self.ledger

    def save(self, ledger: ManagerLedger) -> None:
        self.ledger = ledger


class _NoVideo:
    async def prove(self, criterion) -> ProofResult:
        return ProofResult(criterion.id, False, "no *.mp4 in the workspace")


class _AlwaysSendBack:
    """A manager model that answers "send it back" to everything — the worst case."""

    def __init__(self, kind: str = REDIRECT) -> None:
        self.kind = kind
        self.briefs = []

    async def review(self, brief) -> ManagerVerdict:
        self.briefs.append(brief)
        return ManagerVerdict(self.kind, directive="Call comfy_run on video.api.json.", criterion_id="c1")

    async def draft_contract(self, brief):  # not reached: the contract is already in force
        raise AssertionError("no contract is drafted here")


def _service(pm) -> tuple[ManagerCheckpointService, RunDigestRecorder, _Store]:
    async def emit(event, payload):
        return None

    recorder = RunDigestRecorder()
    store = _Store()
    svc = ManagerCheckpointService(
        project_manager=pm,
        ledger_store=store,
        proof_runner=_NoVideo(),
        sheet=CapabilitySheet(tools=(("comfy_run", "run a workflow"),)),
        policy=EscalationPolicy(),
        recorder=recorder,
        emit=emit,
        is_runtime=lambda m: False,
    )
    svc._engaged = True  # a contract is in force, as in the replayed chat
    return svc, recorder, store


MESSAGES = [
    UserMessage(content="approved — run it"),
    AssistantMessage(content=[TextContent(text="Please add @character_a and @character_b on the Inputs tab.")]),
]


def _finish(svc) -> str | None:
    return asyncio.run(svc.before_finish(MESSAGES))


def test_a_stop_is_reviewed_once_then_pushed_once_then_stands():
    pm = _AlwaysSendBack()
    svc, recorder, store = _service(pm)
    recorder.record("comfy_run", {"workflow": "video.api.json"}, True, "reference slots empty: @character_a")

    assert _finish(svc), "the first stop is reviewed, and the manager may send it back"
    assert len(pm.briefs) == 1

    # The developer answered with words, no tool call: one firmer push, no second review.
    assert "Take it now" in (_finish(svc) or "")
    assert len(pm.briefs) == 1

    # Still no action: the stop stands, and nothing reviews it again.
    assert _finish(svc) is None
    assert len(pm.briefs) == 1
    assert store.ledger.awaiting_user
    assert store.ledger.decisions[-1]["kind"] == WAIT


def test_a_send_back_that_is_acted_on_gets_a_normal_review():
    pm = _AlwaysSendBack()
    svc, recorder, _ = _service(pm)
    recorder.record("comfy_run", {}, True, "slots empty")
    assert _finish(svc)
    # The developer did act on it: that is new evidence, so the next stop is reviewed again.
    recorder.record("comfy_validate", {}, False, "compiles")
    assert _finish(svc)
    assert len(pm.briefs) == 2


def test_a_wait_verdict_ends_the_run_and_the_brief_carries_why_it_stopped():
    pm = _AlwaysSendBack(kind=WAIT)
    svc, recorder, store = _service(pm)
    recorder.record("comfy_run", {"workflow": "video.api.json"}, True, "reference slots empty: @character_a")
    assert _finish(svc) is None
    assert store.ledger.awaiting_user
    rendered = pm.briefs[0].render()
    assert "WHY THE DEVELOPER STOPPED" in rendered
    assert "reference slots empty" in rendered
    assert pm.briefs[0].checkpoint == FINISH

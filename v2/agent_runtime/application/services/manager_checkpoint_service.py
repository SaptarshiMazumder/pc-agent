"""ManagerCheckpointService — the project manager beside every agent's run.

WHY IT EXISTS. An agent working a long task holds the whole problem in one context, and that is
exactly how it gets lost: it hits an obstacle, reasons itself into "this is a blocker", and ends
the turn with an honest report of a thing it did not build. More instructions to the same model in
the same context change nothing. A manager with a DIFFERENT view does: it holds the goal the user
agreed to, knows what the organisation can do, looks at what actually ran — never the developer's
transcript — and decides whether the developer is really blocked or just down the wrong hole.

HOW IT BEHAVES, like a manager and not a micromanager:
  * SMALL WORK NEVER SEES IT. It engages only once a run is clearly a piece of work: a plan of two
    or more steps, or ENGAGE_AFTER_CALLS tool calls, or a contract already in force for this chat.
  * ENGAGE: it drafts a contract from the user's own words — criteria that can be checked. Work
    that creates something lasting or spends money waits for the user's approval first.
  * MILESTONES AND DRIFT: when the developer completes a plan step, or activity stops producing
    anything, it reviews and either stays quiet or redirects.
  * FINISH: the developer cannot end the turn while a criterion is unproven. The proofs are run
    here, not reported by the developer. Repeated failure on one criterion becomes a RETHINK, and
    exhausted rethinks become a question to the user with the evidence of what was tried
    (EscalationPolicy — rules, not the model's mood).

LONG WORK DOES NOT DIE ON `length`. Once contracted work fills HANDOFF_AT of the model's context,
the old history is sent as a handoff written from the manager's records (WorkHandoff) plus the
recent stretch verbatim, and a run that reaches its iteration cap with the contract unfinished gets
another budget, up to MAX_ITERATION_EXTENSIONS.

IT FAILS LOUD, NOT CLOSED. If the manager model cannot answer, the failure is emitted to the
client and logged; that checkpoint passes ungated. A run is never silently treated as approved.

ONE SERVICE PER RUN. Built by the composition root from a ManagerRunScope; its ledger persists
beside the session so the next run resumes against the same contract.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from collections.abc import Awaitable, Callable

from agent_runtime.application.interfaces.manager_ledger_store import ManagerLedgerStore
from agent_runtime.application.interfaces.project_manager import ManagerUnavailable, ProjectManager
from agent_runtime.application.interfaces.proof_runner import ProofRunner
from agent_runtime.application.services.run_digest_recorder import RunDigestRecorder
from agent_runtime.domain.app_context import is_app_context
from agent_runtime.domain.capability_sheet import CapabilitySheet
from agent_runtime.domain.deliverable_contract import DeliverableContract
from agent_runtime.domain.escalation_policy import ESCALATE_DIRECTIVE, EscalationPolicy
from agent_runtime.domain.manager_brief import (
    DRIFT,
    ENGAGE,
    FINISH,
    MILESTONE,
    REPLY,
    ManagerBrief,
)
from agent_runtime.domain.manager_ledger import ManagerLedger
from agent_runtime.domain.manager_verdict import (
    AWAIT_APPROVAL,
    CONTINUE,
    DONE,
    ESCALATE,
    MANAGER_PREFIX,
    REDIRECT,
    RETHINK,
    ManagerVerdict,
)
from agent_runtime.domain.messages import (
    RUNTIME,
    AssistantMessage,
    Message,
    ToolResultMessage,
    UserMessage,
)
from agent_runtime.domain.proof_result import ProofResult
from agent_runtime.domain.work_handoff import WorkHandoff

log = logging.getLogger("agentd.manager")

ENGAGE_AFTER_CALLS = 8  # tool calls before a run counts as a piece of work
MIN_PLAN_STEPS = 2  # a plan this long also does
MAX_FORCED_CONTINUES = 12  # per run: the manager may send the developer back this many times
PLAN_TOOL = "update_plan"
HANDOFF_AT = 0.6  # fraction of the model's context used before old history becomes a handoff
KEEP_RECENT_MESSAGES = 30  # the stretch of the work sent verbatim after the handoff
MAX_ITERATION_EXTENSIONS = 3  # contracted work may run this many iteration budgets past the cap

APPROVAL_DIRECTIVE = (
    "Before building anything, the user must approve the plan. Send them EXACTLY the message "
    "between the lines — word for word, nothing before or after it. Do not describe what you "
    "inspected, the starting template, files or checks. Then END YOUR TURN and wait."
)
_RULE = "-" * 40

# THE AGENT'S OWN CARD, when it has one. An agent with an approval tool of its own (Comfy
# Penguin's ask_user card: the brief, the paid services, their prices) already stops for the
# user's go; a second, separate plan message made the person approve the same work twice. So
# the plan rides INSIDE that card, and the one answer approves both.
APPROVAL_IN_OWN_TOOL = (
    "Before building anything, the user must approve the plan. Ask for it with your own `{tool}` "
    "card: include the points between the lines in it, word for word, alongside what the card "
    "already asks. Do NOT send them as a separate message. Then END YOUR TURN and wait."
)


class ManagerCheckpointService:
    def __init__(
        self,
        *,
        project_manager: ProjectManager,
        ledger_store: ManagerLedgerStore,
        proof_runner: ProofRunner,
        sheet: CapabilitySheet,
        policy: EscalationPolicy,
        recorder: RunDigestRecorder,
        emit: Callable[[str, dict], Awaitable[None]],
        is_runtime: Callable[[UserMessage], bool],
        approval_tool: str = "",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """:param approval_tool: the agent's own approval tool (a checkpoint tool), or "" — the
        plan is then approved inside that tool's card rather than in a message of the manager's."""
        self._pm = project_manager
        self._store = ledger_store
        self._proofs = proof_runner
        self._sheet = sheet
        self._policy = policy
        self._recorder = recorder
        self._emit = emit
        self._is_runtime = is_runtime
        self._approval_tool = approval_tool
        self._clock = clock
        self._ledger: ManagerLedger = ledger_store.load()
        self._engaged = self._ledger.contract is not None and not self._fulfilled()
        self._completed_steps = 0
        self._forced = 0
        self._run_start = 0  # where THIS run's messages begin; a finished task's old plan is not
        #                      this run's plan, and must not make a small question look like work
        self._context_used = 0.0
        self._handing_off = False  # sticky: once the history is condensed it stays condensed, or
        #                            the full history would go straight back out and refill it
        self._extensions = 0

    # ------------------------------------------------------------------ engine-facing

    def record_tool(self, name: str, args: dict, is_error: bool, result_text: str) -> None:
        self._recorder.record(name, args, is_error, result_text)

    async def on_start(self, messages: list[Message]) -> str | None:
        """A new run in a chat that already has a contract: settle a pending approval from the
        user's reply, or restate the contract so the developer starts from what was agreed."""
        self._run_start = len(messages)
        contract = self._ledger.contract
        if contract is None or self._fulfilled():
            return None
        if contract.pending_approval:
            return await self._settle_reply(messages)
        self._ledger.awaiting_user = False  # the user has answered whatever was put to them
        self._store.save(self._ledger)
        return self._say("Contract in force for this work (each criterion is checked, not taken "
                         "on your word):\n" + contract.render())

    async def after_step(self, messages: list[Message], drift: list[str]) -> str | None:
        signals = self._recorder.drift() + [d for d in drift if d]
        if not self._engaged:
            if not self._should_engage(messages):
                return None
            self._engaged = True
            return await self._engage(messages)
        contract = self._ledger.contract
        if contract is None or not contract.active or self._ledger.awaiting_user:
            return None
        milestone = self._completed_plan_steps(messages) > self._completed_steps
        self._completed_steps = self._completed_plan_steps(messages)
        if not (milestone or signals):
            return None
        checkpoint = DRIFT if signals else MILESTONE
        verdict = await self._review(self._brief(checkpoint, messages, drift=signals))
        return self._act(checkpoint, verdict) if verdict else None

    async def before_finish(self, messages: list[Message]) -> str | None:
        contract = self._ledger.contract
        if not self._engaged or contract is None or not contract.active or self._ledger.awaiting_user:
            return None
        if self._forced >= MAX_FORCED_CONTINUES:
            return self._act(FINISH, ManagerVerdict(
                ESCALATE, directive=ESCALATE_DIRECTIVE, reason="continuation budget for this run spent"
            ))
        proofs = [await self._proofs.prove(c) for c in contract.criteria]
        verdict = await self._review(self._brief(FINISH, messages, proofs=proofs))
        if verdict is None:
            return None
        failed = [p for p in proofs if p.passed is False]
        if verdict.kind == DONE and failed:
            # The manager does not get to wave through what the checks disproved.
            first = failed[0]
            verdict = ManagerVerdict(
                REDIRECT,
                directive=f"Criterion {first.criterion_id} is not proven: {first.evidence}. Make it "
                          "true, then finish.",
                criterion_id=first.criterion_id,
                reason="declared done over a failing proof",
            )
        if verdict.kind == DONE:
            self._ledger.contract = contract.fulfilled()
            self._record(FINISH, verdict)
            await self._emit("manager", {"verdict": DONE, "contract": self._ledger.contract.to_dict()})
            return None
        if verdict.kind == CONTINUE:
            # At a finish, CONTINUE means the developer is legitimately pausing for the user.
            self._ledger.awaiting_user = True
            self._record(FINISH, verdict)
            return None
        for p in failed:
            self._ledger.note_failure(p.criterion_id)
        return self._act(FINISH, verdict)

    def note_context(self, fraction_used: float) -> None:
        self._context_used = fraction_used

    def view(self, messages: list[Message]) -> list[Message]:
        """The history to send the model. Unchanged until contracted work fills HANDOFF_AT of the
        context; from then on, a handoff written from the manager's records plus the most recent
        stretch verbatim — what used to end the run on `length` with the work half done."""
        if not self._engaged:
            return messages
        if not self._handing_off and self._context_used < HANDOFF_AT:
            return messages
        start = max(0, len(messages) - KEEP_RECENT_MESSAGES)
        # Never open the window on a tool result: it would arrive without the call it answers.
        while start > 0 and isinstance(messages[start], ToolResultMessage):
            start -= 1
        if start == 0:
            return messages
        self._handing_off = True
        handoff = WorkHandoff(
            user_messages=tuple(self._user_messages(messages)),
            contract=self._ledger.contract,
            plan=tuple(self._plan_lines(messages)),
            files_written=self._recorder.files_written(),
            recent_work=self._recorder.work_log(),
            decisions=tuple(self._ledger.recent_decisions()),
        )
        return [UserMessage(content=handoff.render(), source=RUNTIME), *messages[start:]]

    def extend_iterations(self) -> bool:
        """The run hit its iteration cap. Contracted work that is not finished gets another
        budget — up to MAX_ITERATION_EXTENSIONS — rather than stopping mid-build."""
        contract = self._ledger.contract
        if not self._engaged or contract is None or not contract.active or self._ledger.awaiting_user:
            return False
        if self._extensions >= MAX_ITERATION_EXTENSIONS:
            return False
        self._extensions += 1
        return True

    # ------------------------------------------------------------------ the decisions

    async def _engage(self, messages: list[Message]) -> str | None:
        contract = await self._draft(self._brief(ENGAGE, messages))
        if contract is None:
            return None
        self._ledger.contract = contract
        self._ledger.awaiting_user = contract.pending_approval
        self._store.save(self._ledger)
        await self._emit("manager", {"checkpoint": ENGAGE, "contract": contract.to_dict()})
        if contract.pending_approval:
            return self._ask_approval(contract)
        return self._say("This is what done means for this work; each criterion will be checked "
                         "at the end, not taken on your word:\n" + contract.render())

    async def _settle_reply(self, messages: list[Message]) -> str | None:
        contract = await self._draft(self._brief(REPLY, messages))
        if contract is None:
            return None
        self._ledger.contract = contract
        self._engaged = True
        self._ledger.awaiting_user = contract.pending_approval
        self._store.save(self._ledger)
        await self._emit("manager", {"checkpoint": REPLY, "contract": contract.to_dict()})
        if contract.pending_approval:
            return self._ask_approval(contract, revised=True)
        return self._say("The user approved the plan. Build to this contract; each criterion is "
                         "checked at the end:\n" + contract.render())

    def _ask_approval(self, contract: DeliverableContract, revised: bool = False) -> str:
        """One approval: in the agent's own card when it has one, else the manager's message."""
        if self._approval_tool:
            ask = APPROVAL_IN_OWN_TOOL.format(tool=self._approval_tool)
            return self._say(f"{ask}\n{_RULE}\n{contract.plan_points()}\n{_RULE}")
        return self._say(f"{APPROVAL_DIRECTIVE}\n{_RULE}\n{contract.present(revised=revised)}\n{_RULE}")

    def _act(self, checkpoint: str, verdict: ManagerVerdict) -> str | None:
        verdict = self._policy.adjust(verdict, self._ledger)
        if verdict.kind == RETHINK and verdict.criterion_id:
            self._ledger.note_rethink(verdict.criterion_id)
        if verdict.kind in (ESCALATE, AWAIT_APPROVAL):
            self._ledger.awaiting_user = True
        self._record(checkpoint, verdict)
        if verdict.kind in (CONTINUE, DONE) or not verdict.directive:
            return None
        self._forced += 1
        return self._say(verdict.directive)

    # ------------------------------------------------------------------ the manager calls

    async def _draft(self, brief: ManagerBrief) -> DeliverableContract | None:
        try:
            return await self._pm.draft_contract(brief)
        except ManagerUnavailable as e:
            await self._surface(brief.checkpoint, e)
            return None

    async def _review(self, brief: ManagerBrief) -> ManagerVerdict | None:
        try:
            return await self._pm.review(brief)
        except ManagerUnavailable as e:
            await self._surface(brief.checkpoint, e)
            return None

    async def _surface(self, checkpoint: str, error: Exception) -> None:
        log.warning("manager unavailable at %s: %s", checkpoint, error)
        await self._emit("manager", {"checkpoint": checkpoint, "error": str(error)})

    # ------------------------------------------------------------------ reading the run

    def _brief(
        self,
        checkpoint: str,
        messages: list[Message],
        drift: list[str] | None = None,
        proofs: list[ProofResult] | None = None,
    ) -> ManagerBrief:
        return ManagerBrief(
            checkpoint=checkpoint,
            user_messages=tuple(m for m in self._user_messages(messages) if not is_app_context(m)),
            # Only where the contract is WRITTEN (engage, reply): it says where the work happens.
            # In reviews it read as a requirement — five redirects demanded an "initial
            # inspection" the person never asked for — so reviews never see it.
            app_context=(
                tuple(m for m in self._user_messages(messages) if is_app_context(m))
                if checkpoint in (ENGAGE, REPLY)
                else ()
            ),
            contract=self._ledger.contract,
            sheet=self._sheet,
            digest=self._recorder.take(),
            developer_claim=self._developer_claim(messages),
            developer_plan=tuple(self._plan_lines(messages)),
            proofs=tuple(proofs or ()),
            drift=tuple(drift or ()),
            decisions=tuple(self._ledger.recent_decisions()),
        )

    def _should_engage(self, messages: list[Message]) -> bool:
        return (
            self._recorder.total_calls >= ENGAGE_AFTER_CALLS
            or len(self._plan_lines(messages)) >= MIN_PLAN_STEPS
        )

    def _user_messages(self, messages: list[Message]) -> list[str]:
        """The person's words — WITH what they attached, named. Text alone once had the manager
        ask the user to upload the portraits they had already attached."""
        out = []
        for m in messages:
            if not isinstance(m, UserMessage) or self._is_runtime(m):
                continue
            files = ", ".join(Path(a.path).name for a in m.attachments)
            text = m.content + (f"\n[attached: {files}]" if files else "")
            if text.strip():
                out.append(text)
        return out

    @staticmethod
    def _developer_claim(messages: list[Message]) -> str:
        for m in reversed(messages):
            if isinstance(m, AssistantMessage) and m.text.strip():
                return m.text.strip()
        return ""

    def _latest_plan(self, messages: list[Message]) -> list[dict]:
        for m in reversed(messages[self._run_start:]):
            if isinstance(m, AssistantMessage):
                for call in reversed(m.tool_calls):
                    if call.name == PLAN_TOOL:
                        return list((call.arguments or {}).get("plan") or [])
        return []

    def _plan_lines(self, messages: list[Message]) -> list[str]:
        return [f"{s.get('status', '?')}: {s.get('step', '')}" for s in self._latest_plan(messages)]

    def _completed_plan_steps(self, messages: list[Message]) -> int:
        return sum(1 for s in self._latest_plan(messages) if s.get("status") == "completed")

    # ------------------------------------------------------------------ bookkeeping

    def _fulfilled(self) -> bool:
        contract = self._ledger.contract
        return contract is not None and not contract.active and not contract.pending_approval

    def _record(self, checkpoint: str, verdict: ManagerVerdict) -> None:
        self._ledger.record(checkpoint, verdict, self._clock())
        self._store.save(self._ledger)

    @staticmethod
    def _say(text: str) -> str:
        return f"{MANAGER_PREFIX} {text}"


__all__ = ["ENGAGE_AFTER_CALLS", "MAX_FORCED_CONTINUES", "ManagerCheckpointService"]

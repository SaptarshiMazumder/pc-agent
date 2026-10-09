"""Whether something that costs money and belongs to no campaign (a new cast member's character
sheet) may be generated — the user approves the MODEL, by clicking, as for
every other run (RunApprovals).

With the plugin's `generation_approval` at "ask" (the default), a request on the agent's word
alone makes nothing: it becomes a PROPOSAL, keyed by what it would make (the cast member's name),
shown in the studio, where the user picks the model and generates it, or
dismisses it. The studio's click first records a one-time approval for exactly that key and
model, and the run must carry it. Recording one is a WINDOW action (the approval tools check that).
"""

from __future__ import annotations

from typing import Callable

from ad_generation.application.interfaces.proposal_store import ProposalStore
from ad_generation.application.run_approvals import NeedsApproval

_KEEP = 20


class OneTimeApprovals:
    def __init__(self, store: ProposalStore, new_token: Callable[[], str]) -> None:
        self._store = store
        self._new_token = new_token

    def proposals(self) -> list[dict]:
        return list(self._store.proposals().values())

    def propose(self, key: str, proposal: dict) -> None:
        """`proposal` must carry `model`; the rest is what the studio shows and sends back."""
        proposals = self._store.proposals()
        proposals[key] = {**proposal, "key": key}
        self._store.save_proposals(proposals)

    def approve(self, key: str, model: str) -> str:
        """The user clicked: this, on this model, may be made once. -> its token."""
        if not key or "/" not in model:
            raise ValueError("an approval names what it makes and the provider/model")
        token = self._new_token()
        self._store.save_approvals([*self._store.approvals()[-(_KEEP - 1):], {"token": token, "key": key, "model": model}])
        return token

    def admit(self, key: str, model: str, token: str, mode: str) -> None:
        """Let the run go ahead, or raise NeedsApproval. An approval is used up by its run; a run
        clears its proposal."""
        if mode != "auto":
            approvals = self._store.approvals()
            match = next((a for a in approvals if token and a.get("token") == token), None)
            if match is None or match.get("key") != key or match.get("model") != model:
                raise NeedsApproval(f"the user has not approved making '{key}' on {model}")
            self._store.save_approvals([a for a in approvals if a is not match])
        self.dismiss(key)

    def dismiss(self, key: str) -> None:
        proposals = self._store.proposals()
        if proposals.pop(key, None) is not None:
            self._store.save_proposals(proposals)

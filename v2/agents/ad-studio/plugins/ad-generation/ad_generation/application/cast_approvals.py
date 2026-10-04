"""Whether a new cast member's character sheet may be generated — the user approves the MODEL, by
clicking, as for every other run (RunApprovals).

With the plugin's `generation_approval` at "ask" (the default), cast_create on the agent's word
alone makes nothing: it becomes a PROPOSAL — the name, the description, the face it was given —
shown in the studio, where the user picks the model and generates it, or dismisses it. The
studio's Generate first records a one-time approval for exactly that name and model, and the run
must carry it. Recording one is a WINDOW action (cast_approval checks that).
"""

from __future__ import annotations

from typing import Callable

from ad_generation.application.interfaces.cast_proposal_store import CastProposalStore
from ad_generation.application.run_approvals import NeedsApproval

_KEEP = 20


class CastApprovals:
    def __init__(self, store: CastProposalStore, new_token: Callable[[], str]) -> None:
        self._store = store
        self._new_token = new_token

    def proposals(self) -> list[dict]:
        return list(self._store.proposals().values())

    def propose(self, name: str, description: str, references: list[str], model: str) -> None:
        proposals = self._store.proposals()
        proposals[name] = {"name": name, "description": description, "references": list(references), "model": model}
        self._store.save_proposals(proposals)

    def approve(self, name: str, model: str) -> str:
        """The user clicked: this cast member, on this model, may be made once. -> its token."""
        if not name or "/" not in model:
            raise ValueError("an approval names the cast member and the provider/model")
        token = self._new_token()
        self._store.save_approvals([*self._store.approvals()[-(_KEEP - 1):], {"token": token, "name": name, "model": model}])
        return token

    def admit(self, name: str, model: str, token: str, mode: str) -> None:
        """Let the run go ahead, or raise NeedsApproval. An approval is used up by its run; a run
        clears the name's proposal."""
        if mode != "auto":
            approvals = self._store.approvals()
            match = next((a for a in approvals if token and a.get("token") == token), None)
            if match is None or match.get("name") != name or match.get("model") != model:
                raise NeedsApproval(f"the user has not approved making cast member '{name}' on {model}")
            self._store.save_approvals([a for a in approvals if a is not match])
        self.dismiss(name)

    def dismiss(self, name: str) -> None:
        proposals = self._store.proposals()
        if proposals.pop(name, None) is not None:
            self._store.save_proposals(proposals)

"""The approval check the generating tools share: let the run go ahead, or turn it into a proposal
the user approves in the studio — and tell the agent plainly that nothing was made."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import ToolResult
from agent_runtime.application.run_context import current_run_context

from ad_generation.application.run_approvals import NeedsApproval, RunApprovals


def from_window() -> bool:
    """Whether this call came from the studio itself (a click), not from a model turn."""
    ctx = current_run_context()
    return bool(getattr(ctx, "direct_invoke", False)) if ctx else False


def gate(
    approvals: RunApprovals, campaign: str, step: str, tool: str, pinned: dict, token: str, proposal: dict
) -> ToolResult | None:
    """None: go ahead. Otherwise the result to return instead of running: the run is now a
    proposal on its step, waiting for the user's click."""
    try:
        approvals.admit(campaign, step, tool, pinned, token)
        return None
    except NeedsApproval:
        approvals.propose(campaign, step, tool, proposal)
        model = pinned.get("model") or "the default model"
        return ToolResult.text(
            f"Nothing was generated. This campaign asks the user to approve each run: it is proposed in "
            f"the studio on step '{step}' ({tool}, {model}), where they choose the model and press Generate, "
            "or dismiss it. Tell them in one line that it is ready for their go, then end your turn. Do not "
            "call this again for it.",
            details={"proposed": {"step": step, "tool": tool, "args": proposal}},
        )

"""campaign_ask — the question at a campaign's gate.

A CHECKPOINT. `checkpoint = True` makes the daemon stamp the conversation the moment this
returns ("shown") and again when the user's next message arrives ("answered"); campaign_run
moves past the gate only after both, in that order (DaemonCheckpointLedger). The agent can ask;
it cannot answer for the user.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.domain.campaign_progress import DONE
from ad_generation.presentation.generation_backend_resolver import PLUGIN

# More rows than this is a menu, not a question.
_MAX_QUESTIONS = 8


class CampaignAskTool(Tool):
    name = "campaign_ask"
    label = "Ask at the gate"
    plugin = PLUGIN
    checkpoint = True
    default_retryable = False
    description = (
        "Ask the user at the gate a campaign waits at, after showing them its results: one "
        "question per shot (one for the brief at the brief gate), each with the default you "
        "would take — `keep` unless you have a reason. Then END THE TURN: the answer is the "
        "user's next message, and campaign_run refuses to move on until it has arrived."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "questions"],
        "properties": {
            "campaign": {"type": "string", "description": "The campaign id."},
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": _MAX_QUESTIONS,
                "items": {
                    "type": "object",
                    "required": ["about", "question", "default"],
                    "properties": {
                        "about": {"type": "string", "description": "The shot id, or \"brief\"."},
                        "question": {"type": "string"},
                        "default": {"type": "string", "description": "What you will do if the user just approves."},
                    },
                },
            },
        },
    }

    def __init__(self, config, store: CampaignStore) -> None:
        self.config = config
        self._store = store

    async def execute(self, tool_call_id, params, abort, on_update=None):
        campaign = str(params.get("campaign") or "").strip()
        try:
            gate = self._store.progress(campaign).gate
        except KeyError as e:
            return ToolResult.text(f"campaign_ask refused: {e}", is_error=True)
        if gate == DONE:
            return ToolResult.text(f"campaign_ask refused: {campaign} is finished; there is no gate to ask about", is_error=True)
        questions = [q for q in params.get("questions") or [] if isinstance(q, dict)]
        if not questions:
            return ToolResult.text("campaign_ask refused: no questions", is_error=True)
        lines = [f"{campaign} · {gate} gate"]
        for q in questions:
            lines.append(f"  {q.get('about')}: {q.get('question')} [{q.get('default')}]")
        lines.append(
            "Asked. The answer arrives as the user's NEXT message — end this turn now: no more "
            "tool calls."
        )
        return ToolResult.text(
            "\n".join(lines), details={"campaign": campaign, "gate": gate, "questions": questions}
        )

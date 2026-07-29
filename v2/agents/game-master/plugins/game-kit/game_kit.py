"""game-kit — the Game Master agent's OWN tools (agent-private plugin).

Ships INSIDE agents/game-master/plugins/ and travels with the agent in its .agentpkg. Two tools,
deliberately one of each kind so the sandbox seam can be exercised end-to-end:

  * ``roll_dice``     — a NORMAL tool: pure computation, no LLM, no network, no secrets. Parses
                        RPG dice notation ("2d6", "1d20+3", "d20") and returns the rolls + total.
  * ``narrate_scene`` — a MODEL-BEARING tool: ``needs_model = True``. It resolves its text model the
                        uniform way (``resolve_model`` → inherits the brain when unset) and makes a
                        single ``text_complete`` call to turn a bare setup into vivid prose.

Both are agent-private, so with ``AGENTD_SANDBOX_PLUGINS=1`` they run behind the PluginSandbox
(today: in-process passthrough) while the shared/built-in tools keep running in-process directly.
"""

from __future__ import annotations

import asyncio
import random
import re

from agentd.application.interfaces.tool import Tool, ToolResult

# ── roll_dice ──────────────────────────────────────────────────────────────────────────────────
# NdM(+/-K): N dice (default 1) of M sides, optional flat modifier. e.g. "2d6", "d20", "3d8-2".
_DICE_RE = re.compile(r"^\s*(\d*)\s*[dD]\s*(\d+)\s*([+-]\s*\d+)?\s*$")
_MAX_DICE, _MAX_SIDES = 100, 1000  # sanity caps so a typo can't ask for a billion rolls


class RollDiceTool(Tool):
    name = "roll_dice"
    plugin = "game-kit"
    label = "Roll dice"
    concurrency = "parallel"
    description = (
        "Roll RPG dice from standard notation and return each die + the total. "
        "notation examples: '2d6' (two six-sided), 'd20' (one twenty-sided), '3d8+2' "
        "(three eight-sided plus 2). No LLM, no network — a pure chance roll."
    )
    parameters = {
        "type": "object",
        "required": ["notation"],
        "properties": {
            "notation": {
                "type": "string",
                "description": "dice notation like '2d6', 'd20', or '3d8+2'",
            }
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None) -> ToolResult:
        notation = str(params.get("notation") or "").strip()
        m = _DICE_RE.match(notation)
        if not m:
            return ToolResult.text(
                f"couldn't read dice notation {notation!r}. Use forms like '2d6', 'd20', '3d8+2'.",
                is_error=True,
            )
        count = int(m.group(1)) if m.group(1) else 1
        sides = int(m.group(2))
        modifier = int(m.group(3).replace(" ", "")) if m.group(3) else 0
        if count < 1 or count > _MAX_DICE or sides < 2 or sides > _MAX_SIDES:
            return ToolResult.text(
                f"out of range: roll 1–{_MAX_DICE} dice of 2–{_MAX_SIDES} sides.", is_error=True
            )
        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls) + modifier
        mod_str = f" {'+' if modifier >= 0 else '-'} {abs(modifier)}" if modifier else ""
        text = f"{notation} → rolls {rolls}{mod_str} = {total}"
        return ToolResult.text(
            text, details={"notation": notation, "rolls": rolls, "modifier": modifier, "total": total}
        )


# ── narrate_scene ──────────────────────────────────────────────────────────────────────────────
class NarrateSceneTool(Tool):
    name = "narrate_scene"
    plugin = "game-kit"
    label = "Narrate scene"
    concurrency = "parallel"
    # MODEL-BEARING: declares it needs a text model; resolve_model inherits the brain when unset,
    # or a config/agent.toml override (plugins.game-kit.tools.narrate_scene.model) wins.
    needs_model = True
    model_kind = "text"
    default_model = ""  # "" => inherit the brain model
    description = (
        "Turn a short scene setup into vivid, sensory second-person RPG narration (a few "
        "sentences). Uses an LLM. Example setup: 'the party enters a flooded crypt at midnight'."
    )
    parameters = {
        "type": "object",
        "required": ["setup"],
        "properties": {
            "setup": {
                "type": "string",
                "description": "a brief description of the scene to narrate",
            },
            "tone": {
                "type": "string",
                "description": "optional mood, e.g. 'eerie', 'heroic', 'comic' (default: vivid)",
            },
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None) -> ToolResult:
        setup = str(params.get("setup") or "").strip()
        if not setup:
            return ToolResult.text("narrate_scene needs a 'setup' to describe.", is_error=True)
        tone = str(params.get("tone") or "vivid").strip()

        # Resolve the model uniformly — a text tool inherits the brain when nothing is configured.
        from agentd.application.tool_models import brain_model

        model = self.resolve_model(self.config) or brain_model(self.config)

        prompt = (
            "You are a tabletop RPG game master. Write a short, vivid, second-person, present-tense "
            f"scene description (2–4 sentences, {tone} tone) for this setup. Concrete sensory detail; "
            "no dice, no rules talk; end on a beat that invites the player to act.\n\n"
            f"Setup: {setup}"
        )

        from agentd.infrastructure.llm.oneshot import text_complete

        try:
            narration = await asyncio.to_thread(
                text_complete, model=model, prompt=prompt, max_tokens=220, timeout=60
            )
        except Exception as e:  # noqa: BLE001 — a model/proxy failure is reported, never crashes the turn
            return ToolResult.text(f"narration failed ({type(e).__name__}): {e}", is_error=True)
        narration = (narration or "").strip()
        if not narration:
            return ToolResult.text("the model returned an empty narration.", is_error=True)
        return ToolResult.text(narration, details={"model": model, "setup": setup, "tone": tone})


def register(api, ctx):
    api.register_tool(RollDiceTool(ctx.config))
    api.register_tool(NarrateSceneTool(ctx.config))

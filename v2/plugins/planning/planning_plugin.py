"""Built-in 'planning' bundle — the ``update_plan`` tool (OpenClaw update-plan-tool parity).

Migrated OUT of agentd core: a tool *implementation* lives here in ``plugins/``, not in the
framework. It imports only the framework surface (``Tool`` / ``ToolResult``). Always registered
(no deps/config gating). Its model-facing guidance is the tool description + the
``tools/update_plan.md`` card (the ## Planning block), unchanged by the move.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

_PLAN_STEP_STATUSES = ("pending", "in_progress", "completed")
_STATUS_ICON = {"completed": "✓", "in_progress": "▶", "pending": "○"}


def render_plan(plan: list, explanation: str = "") -> str:
    """The plan as a compact, human-readable checklist every client can show (✓ done · ▶ in progress
    · ○ to do). This is the tool's OUTPUT so it renders as real content, not an empty "(no output)"."""
    lines: list[str] = []
    for s in plan:
        if not isinstance(s, dict):
            continue
        icon = _STATUS_ICON.get(str(s.get("status", "")).strip().lower(), "○")
        step = str(s.get("step", "")).strip()
        if step:
            lines.append(f"{icon} {step}")
    body = "\n".join(lines)
    return f"{explanation}\n\n{body}" if explanation else body


class UpdatePlanTool(Tool):
    name = "update_plan"
    label = "Update Plan"
    default_retryable = False
    # OpenClaw describeUpdatePlanTool() core + explicit decompose-with-tools guidance.
    description = (
        "Update current run plan. Use for non-trivial multi-step work; keep the plan "
        "current while executing. BREAK THE TASK DOWN into the smallest individual "
        "steps, and for EACH step name the specific tool it will use — e.g. "
        '"web_search: find X", "browser: open Y and read Z", "computer: click W", '
        '"read: open file F". Short steps; max one `in_progress`; mark steps '
        "completed as you finish them. Skip planning only for simple one-step work."
    )
    parameters = {
        "type": "object",
        "required": ["plan"],
        "properties": {
            "explanation": {
                "type": "string",
                "description": "Short note: what changed.",
            },
            "plan": {
                "type": "array",
                "minItems": 1,
                "description": "Ordered steps; max one in_progress.",
                "items": {
                    "type": "object",
                    "required": ["step", "status"],
                    "additionalProperties": True,
                    "properties": {
                        "step": {"type": "string", "description": "Short step."},
                        "status": {
                            "type": "string",
                            "enum": list(_PLAN_STEP_STATUSES),
                            "description": "pending | in_progress | completed.",
                        },
                    },
                },
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        plan = params.get("plan")
        if not isinstance(plan, list) or not plan:
            return ToolResult.text("plan required", is_error=True)
        # At most one in_progress (else progress state is ambiguous) — OpenClaw rule.
        in_progress = sum(
            1 for s in plan if isinstance(s, dict) and s.get("status") == "in_progress"
        )
        if in_progress > 1:
            return ToolResult.text("plan can contain at most one in_progress step", is_error=True)
        details = {"status": "updated", "plan": plan}
        explanation = (params.get("explanation") or "").strip()
        if explanation:
            details["explanation"] = explanation
        # Return the plan as a readable checklist (not empty content, which every client showed as
        # "(no output)"). `details` keeps the structured plan too. A small plan echo is cheap and lets
        # the model + user track progress — same as Claude's todo tool.
        return ToolResult.text(render_plan(plan, explanation), details=details, is_error=False)


# Strong planning DIRECTIVE — system-prompt guidance contributed by this plugin (OpenClaw's
# `systemPromptAddition` model), shown only when update_plan is in the turn's toolset. The tool's
# `description` self-describes the tool; this is the behavioral nudge that belongs in the prompt.
_PLANNING_GUIDANCE = (
    "## Planning\n"
    "For ANY task that takes more than one step, your FIRST action MUST be to call `update_plan` — "
    "do NOT start the work before you have a plan. BREAK THE TASK DOWN into the smallest concrete "
    "steps, and for EACH step name the specific tool it uses. Trigger planning whenever the task: "
    "needs more than one tool, has more than ~2 steps, processes MULTIPLE items (several people / "
    "files / links), or reads as 'do X, then Y, then Z'. Keep the plan current with `update_plan` "
    "as you go (mark steps in_progress / completed), and use the BEST tool per step — `browser` (it "
    "is SIGNED IN via a persistent profile) or `web_search` for the web; `computer` ONLY when a task "
    "truly needs the real desktop GUI. ONLY skip planning for a genuinely simple, single-step request.\n"
    'Example - "summarize the 3 latest posts on a blog into a file":\n'
    "  1. web_fetch: fetch the blog index; note the 3 latest post URLs\n"
    "  2. web_fetch: fetch each post; extract the key points\n"
    "  3. write: save the summaries to a file\n"
    "Pick each step's tool by what it needs (public page vs signed-in/blocked); follow the "
    "web-access rules above."
)


def _planning_section(tools, agent, config) -> str:
    return _PLANNING_GUIDANCE if any(getattr(t, "name", "") == "update_plan" for t in tools) else ""


def register(api, ctx):
    """Always-on: a config-only tool, plus the planning directive (shown when update_plan is in scope)."""
    api.register_tool(UpdatePlanTool())
    api.register_prompt_section(_planning_section)

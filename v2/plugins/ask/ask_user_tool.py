"""``ask_user`` — the ONE way an agent stops to ask before it spends the user's money or builds
around guesses.

WHY A TOOL AND NOT A CONVENTION. The ask used to be a fenced block in prose (```approve, with
`id | service | purpose` lines; or a ```suggest block whose first chip began "You decide"). Every
model spelled it a little differently — one wrote ```text, one dropped the pipes, one put it in
the middle of the message — and each variant rendered as a code block instead of checkboxes and,
worse, was never recognised by the daemon as an ask, so the gate that keeps a paid render from
running before consent never armed. A tool call has a schema: every model emits function calls
reliably, the arguments are validated HERE (a service without a price is refused, not rendered),
and the window draws the structured payload rather than parsing text. Nothing about it is style.

THE CALL IS THE STAMP. The class declares ``checkpoint = True``; the daemon stamps the
conversation's checkpoint (infrastructure/checkpoint_marker.present) the moment this tool's
result comes back — not at the end of the turn — so a tool that spends money later in the SAME
turn finds "presented, unanswered" and refuses. The user's next message is the answer.

It imports only the framework surface (``Tool`` / ``ToolResult``), like every plugin here.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

#: More than this is not a question, it is a menu — and a wall of checkboxes gets ticked blindly.
_MAX_ROWS = 8


def _text(value) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _number(value) -> float | None:
    """A price as the model wrote it — 1.85, "1.85", "$1.85", "308,000" — or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().lstrip("$").replace(",", ""))
        except ValueError:
            return None
    return None


def _rows(params: dict, key: str) -> list[dict]:
    value = params.get(key)
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def normalise(params: dict) -> tuple[dict | None, str]:
    """(payload, error). The payload is the ask as the window shows it; the error is what the
    model reads when it asked badly. Strict where money is involved — a service without its
    price is not an ask, it is the bill discovered later — and lenient about spelling."""
    services: list[dict] = []
    for i, raw in enumerate(_rows(params, "services"), 1):
        name, purpose = _text(raw.get("name")), _text(raw.get("purpose"))
        usd, credits = _number(raw.get("usd")), _number(raw.get("credits"))
        if not name:
            return None, f"services[{i}] has no name."
        if not purpose:
            return None, f"service {name!r} says nothing about what it is for in this job."
        if usd is None or credits is None or usd < 0 or credits < 0:
            return None, (
                f"service {name!r} has no price. Every paid service needs `usd` and `credits` — "
                "exact numbers from comfy_price, never a guess."
            )
        if any(s["name"].lower() == name.lower() for s in services):
            return None, f"service {name!r} is listed twice."
        services.append({"name": name, "purpose": purpose, "usd": round(usd, 4), "credits": int(round(credits))})

    questions: list[dict] = []
    for i, raw in enumerate(_rows(params, "questions"), 1):
        question, default = _text(raw.get("question")), _text(raw.get("default"))
        if not question:
            return None, f"questions[{i}] has no question."
        if not default:
            return None, (
                f"question {question!r} has no default. Every question carries the answer you "
                "will use if the user says nothing — that is what makes a one-word reply enough."
            )
        questions.append({"question": question, "default": default})

    workflows: list[dict] = []
    for i, raw in enumerate(_rows(params, "workflows"), 1):
        name, does = _text(raw.get("name")), _text(raw.get("does"))
        if not name or not does:
            return None, f"workflows[{i}] needs both a role name and what it does."
        workflows.append({"name": name, "does": does})

    if not services and not questions:
        return None, (
            "nothing to ask: give the paid services (with prices) and/or the brief-check "
            "questions (with defaults). A free job still has questions."
        )
    for label, rows in (("services", services), ("questions", questions), ("workflows", workflows)):
        if len(rows) > _MAX_ROWS:
            return None, f"{label}: at most {_MAX_ROWS}. Ask about what the design depends on, not everything."

    return {
        "title": _text(params.get("title")),
        "services": services,
        "questions": questions,
        "workflows": workflows,
    }, ""


def render(ask: dict) -> str:
    """The ask as text — what a plain client shows, and what the model reads back. The model's
    line is the first one: the question is in front of the user, and the turn is over."""
    lines = [
        "Asked. The answer arrives as the user's NEXT message — end this turn now: no more "
        "text after this, no more tool calls."
    ]
    if ask["title"]:
        lines.append(ask["title"])
    if ask["services"]:
        lines.append("Paid services (unticked until the user ticks them):")
        for s in ask["services"]:
            lines.append(f"  {s['name']} — {s['purpose']} — ≈${s['usd']:.2f} · {s['credits']:,} credits")
    if ask["questions"]:
        lines.append("Questions (the default in brackets):")
        for i, q in enumerate(ask["questions"], 1):
            lines.append(f"  {i}. {q['question']} [{q['default']}]")
    if ask["workflows"]:
        lines.append("Workflows, in order: " + "; ".join(f"{w['name']} — {w['does']}" for w in ask["workflows"]))
    return "\n".join(lines)


class AskUserTool(Tool):
    name = "ask_user"
    label = "Ask the user"
    default_retryable = False
    #: SELF-DECLARED CHECKPOINT. The daemon reads this off the class when the call returns and
    #: stamps the conversation (checkpoint_marker.present) — the way canvas tools declare
    #: `artifact_action`. Nothing in the daemon knows this tool by name.
    checkpoint = True
    description = (
        "Stop and ask the user before building: the paid services the design uses with their "
        "exact prices, the brief-check questions with the default you would pick for each, and "
        "the workflows you will emit by role name. The window turns the call into checkboxes and "
        "answer boxes; the daemon arms the gate that keeps comfy_install, comfy_node_install and "
        "comfy_run refused until the user replies. Call it ONCE per job, when the design is "
        "settled and before comfy_emit — then END THE TURN. The answer is the user's next "
        "message. There is no other way to ask: not a fenced block, not a table, not a question "
        "in prose."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "One line: what is about to be built.",
            },
            "services": {
                "type": "array",
                "maxItems": _MAX_ROWS,
                "description": (
                    "Every PAID service the design uses, each with its exact cost for THIS job "
                    "from comfy_price. Empty when the job is free/local."
                ),
                "items": {
                    "type": "object",
                    "required": ["name", "purpose", "usd", "credits"],
                    "properties": {
                        "name": {"type": "string", "description": "Service and model, e.g. 'Kling v3 (720p, 8s)'."},
                        "purpose": {"type": "string", "description": "What it does in this job, e.g. 'the final talking-head video'."},
                        "usd": {"type": "number", "description": "Dollars for this job's use of it, as comfy_price printed."},
                        "credits": {"type": "integer", "description": "The same in platform credits, as comfy_price printed."},
                    },
                },
            },
            "questions": {
                "type": "array",
                "maxItems": _MAX_ROWS,
                "description": (
                    "The brief-check: three to six things the design depends on — what the "
                    "output contains, how it is framed, aspect, duration, style — each phrased so "
                    "a one-word answer works."
                ),
                "items": {
                    "type": "object",
                    "required": ["question", "default"],
                    "properties": {
                        "question": {"type": "string"},
                        "default": {"type": "string", "description": "The answer you will use if the user says nothing."},
                    },
                },
            },
            "workflows": {
                "type": "array",
                "maxItems": _MAX_ROWS,
                "description": "Every workflow you will emit, by role name, in the order they run.",
                "items": {
                    "type": "object",
                    "required": ["name", "does"],
                    "properties": {
                        "name": {"type": "string", "description": "The role name, e.g. 'stills'."},
                        "does": {"type": "string", "description": "What it makes, e.g. 'the four angles'."},
                    },
                },
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        ask, error = normalise(params or {})
        if ask is None:
            return ToolResult.text(f"ask_user refused: {error}", is_error=True)
        return ToolResult.text(render(ask), details={"ask": ask}, is_error=False)

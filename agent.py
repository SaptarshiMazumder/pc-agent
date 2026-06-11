"""
The agent loop. One call to run_turn() == one user request carried to completion.

Callbacks let the transport (Discord) react to progress without this module
knowing anything about Discord:
  on_text(str)            -> the model said something; show it to the user
  approve_bash(cmd)->bool -> ask the user to allow a shell command (blocking)
"""
from anthropic import Anthropic

import config
import computer
import control

client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

TOOLS = [
    {"type": config.COMPUTER_TOOL_TYPE, "name": "computer",
     "display_width_px": config.MODEL_DISPLAY_W,
     "display_height_px": config.MODEL_DISPLAY_H, "display_number": 1},
    {"type": config.BASH_TOOL_TYPE, "name": "bash"},
    {"type": config.EDITOR_TOOL_TYPE, "name": config.EDITOR_TOOL_NAME},
]

SYSTEM = (
    "You control the user's real computer through the tools provided. "
    "Take a screenshot first to see the current state, then act step by step, "
    "re-screenshotting after actions to verify results. Be concise in your text. "
    "If a task is ambiguous or destructive, stop and ask before proceeding."
)


def _dispatch(block, approve_bash):
    """Run one tool_use block, return the content for its tool_result."""
    name, inp = block.name, block.input
    if name == "computer":
        return computer.run_computer(inp)
    if name == "bash":
        if config.REQUIRE_BASH_APPROVAL and not approve_bash(inp.get("command", "")):
            return "[user denied this command]"
        return computer.run_bash(inp)
    if name == config.EDITOR_TOOL_NAME:
        return computer.run_editor(inp)
    return f"[unknown tool: {name}]"


def run_turn(messages, on_text, approve_bash):
    """messages: running conversation list (mutated in place). Returns it."""
    for _ in range(config.MAX_ITERATIONS):
        control.check()                      # bail before each model call
        resp = client.beta.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
            betas=[config.BETA_FLAG],
        )
        messages.append({"role": "assistant", "content": resp.content})

        for block in resp.content:
            if block.type == "text" and block.text.strip():
                on_text(block.text)

        if resp.stop_reason != "tool_use":
            break

        results = []
        for block in resp.content:
            if block.type == "tool_use":
                content = _dispatch(block, approve_bash)
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": content})
        messages.append({"role": "user", "content": results})
    else:
        on_text("⚠️ Hit the iteration cap — stopping to avoid runaway cost.")
    return messages

"""computer tool: operate the PC's GUI like a human (a self-contained sub-agent).

The main agent calls this with a high-level TASK; the tool runs its own internal
computer-use loop (screenshot -> vision model -> mouse/keyboard action -> repeat)
via a dedicated computer-use model, and returns a text summary. Decoupled from
the main agent model. OFF by default — registered only when enabled + pyautogui
present (see factory).
"""

from __future__ import annotations

from .. import Tool, ToolResult


class ComputerTool(Tool):
    name = "computer"
    default_timeout_sec = None  # step cap + per-call timeout in the driver govern this
    default_retryable = False
    label = "Computer"
    concurrency = "sequential"  # drives the one real desktop; never run in parallel
    description = (
        "Operate the computer's GUI to accomplish a task, like a human at the keyboard: "
        "open and control ANY application — click, type, scroll, drag, upload files, change "
        "settings — AND drive the user's REAL web browser, which is already signed in to "
        "their accounts. Give ONE high-level task in plain language (e.g. \"open Notepad, "
        "type the meeting notes, and save as notes.txt on the Desktop\", or \"open my "
        "LinkedIn profile and read my headline\"); it runs autonomously and returns a "
        "summary of what it did.\n"
        "When to use this vs the other tools:\n"
        "- Desktop GUI actions (apps, settings, files a human would click) -> use this.\n"
        "- Any website that needs the USER'S OWN login/session or visual interaction "
        "(e.g. 'my LinkedIn', their webmail, a logged-in dashboard, posting/editing on "
        "their account) -> use this. The separate `browser` tool is a FRESH, "
        "NOT-logged-in browser and will hit a sign-in wall on those.\n"
        "- Public, read-only web content (look something up, read an open article) -> "
        "prefer `web_search` / `browser` instead; they're faster."
    )
    parameters = {
        "type": "object",
        "required": ["task"],
        "properties": {
            "task": {
                "type": "string",
                "description": "The high-level task to perform on the PC's GUI, in plain language.",
            }
        },
    }

    def __init__(self, config, provider):
        self.config = config
        self.provider = provider

    async def execute(self, tool_call_id, params, abort, on_update=None):
        from agentd.infrastructure.tools.computer.drivers import GeminiComputerUseDriver

        try:
            driver = GeminiComputerUseDriver(self.provider, self.config.computer_model, self.config)
            summary = await driver.run(params["task"], abort, on_update)
            return ToolResult.text(summary)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"computer error: {type(e).__name__}: {e}", is_error=True)

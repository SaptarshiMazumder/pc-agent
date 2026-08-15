"""verify_app — open the window you just built and find out whether it works.

THE DESCRIPTION IS THE INSTRUCTION. A tool the model does not know when to reach for is a tool
that does not exist, and this one has to be called at a moment where everything already LOOKS
finished: the build printed no errors and the files are on disk. So the description says plainly
that finished-looking is exactly the state this catches.
"""

from __future__ import annotations

from agent_authoring.application.verify_app_service import Step, VerifyError
from agent_runtime.application.interfaces.tool import Tool, ToolResult


class VerifyAppTool(Tool):
    name = "verify_app"
    label = "Verify App"
    default_retryable = False  # it drives a browser; never auto-retry
    description = (
        "OPEN AN AGENT'S WINDOW AND CHECK IT ACTUALLY WORKS. Call this after building or "
        "changing any ui/, and BEFORE telling anyone the agent is done — `validate_agent` proves "
        "it is well-formed and `agentd ask` proves its brain runs; neither one has ever looked "
        "at the screen.\n"
        "What it catches, all of which look like success from your side: assets that 404 (a "
        "blank window), a crash on mount, console errors, a page that renders nothing, a socket "
        "that never connected, a layout that overflows. It also REFUSES if app/src is newer than "
        "ui/, because otherwise you are verifying the previous build.\n"
        "Pass `steps` to drive what you actually built — click the Refresh button, type in the "
        "composer, drop a file — and it re-checks afterwards, because most windows are fine "
        "until you touch them. Generic checks cannot know what YOUR agent is supposed to do; "
        "the steps are how you check that part.\n"
        "It returns screenshots. LOOK AT THEM: an app can pass every check and still be "
        "unusable, and that is the one thing only an image will tell you.\n"
        "THREE OUTCOMES, and the third is not a failure. NOT VERIFIED means a sign-in gate stood "
        "in front of the app, so nothing behind it was checked — the agent is NOT broken and you "
        "must not report it as such, or fix code that was never run. You must also not report "
        "the agent as finished: nothing was checked. Pass `email` and `password` to sign in and "
        "get a real result.\n"
        "If it FAILS: fix the problem and call it again. Do not report an agent as finished "
        "while this still fails."
    )
    parameters = {
        "type": "object",
        "required": ["agent_id"],
        "properties": {
            "agent_id": {"type": "string", "description": "the agent whose window to open"},
            "email": {
                "type": "string",
                "description": "ONLY when a sign-in gate blocks the app and you need to see "
                "behind it. A test account — never a user's real one, and never guessed: ask.",
            },
            "password": {"type": "string", "description": "the password for `email`"},
            "steps": {
                "type": "array",
                "description": "optional interactions to run after the page loads, in order",
                "items": {
                    "type": "object",
                    "required": ["action"],
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["click", "type", "press", "wait"],
                        },
                        "target": {
                            "type": "string",
                            "description": "what to act on: its VISIBLE TEXT (preferred — "
                            "'Refresh', 'Save') or a CSS selector. For `press`, the key name.",
                        },
                        "text": {
                            "type": "string",
                            "description": "for `type`, what to type; for `wait`, milliseconds",
                        },
                    },
                },
            },
        },
    }

    def __init__(self, service):
        self._service = service

    async def execute(self, tool_call_id, params, abort, on_update=None):
        steps = [
            Step(
                action=str(s.get("action") or ""),
                target=str(s.get("target") or ""),
                text=str(s.get("text") or ""),
            )
            for s in (params.get("steps") or [])
            if isinstance(s, dict)
        ]
        try:
            result = self._service.verify(
                str(params.get("agent_id") or "").strip(),
                steps,
                email=str(params.get("email") or "").strip(),
                password=str(params.get("password") or ""),
            )
        except VerifyError as e:
            return ToolResult.text(str(e), is_error=True)
        except RuntimeError as e:
            # The driver could not start (no browser binaries). Its message names the fix.
            return ToolResult.text(f"could not open a browser: {e}", is_error=True)

        # BLOCKED IS NOT AN ERROR. Marking it one makes the model treat a login screen as a bug
        # in the agent and go and "fix" code that was never executed.
        return ToolResult.text(
            _render(result),
            is_error=not result.passed and not result.blocked,
            artifacts=result.screenshots,
        )


def _render(result) -> str:
    head = f"{result.agent_id} — {result.url}"
    if result.steps_run:
        head += f"\nsteps: {', '.join(result.steps_run)}"

    if result.signed_in:
        head += "\nsigned in with the credentials you supplied"

    if result.blocked:
        # Said in full, because the two easy readings are both wrong: it is not a broken agent,
        # and it is not a verified one.
        body = (
            "NOT VERIFIED — a sign-in gate is in front of the app, so nothing behind it was "
            "checked.\n"
            "This is NOT a defect: do not change the agent's code because of it, and do not "
            "report the agent as working either — it has not been tested.\n"
            + "\n".join(f.as_line() for f in result.findings)
        )
    elif result.passed and not result.findings:
        body = "PASSED — the window loaded, rendered, connected, and reported no errors."
    elif result.passed:
        body = "PASSED with warnings:\n" + "\n".join(f.as_line() for f in result.findings)
    else:
        body = f"FAILED — {sum(1 for f in result.findings if f.is_error)} error(s):\n" + "\n".join(
            f.as_line() for f in result.findings
        )

    obs = result.after_steps or result.observation
    extra = []
    if obs and obs.controls:
        # What is ON SCREEN, so the next call can drive it instead of guessing at selectors.
        extra.append("on screen: " + " | ".join(obs.controls[:20]))
    if result.screenshots:
        extra.append("screenshots: " + ", ".join(result.screenshots))
        extra.append(
            "Look at them. Passing every check and being unusable are compatible, and the "
            "image is the only thing that shows the difference."
        )
    return "\n\n".join([head, body, *extra])

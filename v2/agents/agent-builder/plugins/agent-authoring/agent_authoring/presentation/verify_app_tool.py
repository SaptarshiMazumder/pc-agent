"""verify_app — open the window you just built and find out whether it works.

THE DESCRIPTION IS THE INSTRUCTION. A tool the model does not know when to reach for is a tool
that does not exist, and this one has to be called at a moment where everything already LOOKS
finished: the build printed no errors and the files are on disk. So the description says plainly
that finished-looking is exactly the state this catches.
"""

from __future__ import annotations

import asyncio

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
        "It returns the window's ACCESSIBILITY TREE — every role, label, heading and disabled "
        "state, as text. Read it: that is where you see whether the control you built is on "
        "screen, whether it is enabled, and whether the page rendered what you meant. Pass "
        "`screenshot: true` only when the question is about how it LOOKS, because an image costs "
        "roughly fifty times the context and answers fewer questions.\n"
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
            "screenshot": {
                "type": "boolean",
                "description": "take and attach a picture of the window. OFF by default, and "
                "leave it off: you already get the accessibility tree, which answers 'did it "
                "render', 'is the control there', 'is it disabled' — and does it in ~2KB where "
                "an image costs ~114KB of context that is re-sent on every later turn. Turn it "
                "on for the one thing the tree cannot show: how it LOOKS. Overlapping text, "
                "spacing, something off screen, or the user saying the UI is wrong.",
            },
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
            # A WORKER THREAD, because Playwright's sync API refuses to run inside a live asyncio
            # loop — and a tool's `execute` is always inside one. It fails with "It looks like you
            # are using Playwright Sync API inside the asyncio loop", which is invisible from a
            # standalone script: the same code passes there (no loop) and fails in the daemon,
            # which is the only place it runs for real.
            #
            # A thread is the whole fix. The alternative — async_playwright — would turn the
            # driver, the service, the protocol and every test fake async to reach the same
            # behaviour, and this call is one blocking operation with nothing to interleave.
            result = await asyncio.to_thread(
                self._service.verify,
                str(params.get("agent_id") or "").strip(),
                steps,
                str(params.get("email") or "").strip(),
                str(params.get("password") or ""),
                bool(params.get("screenshot")),
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
            # Only ever the images actually taken — the driver skips them unless asked, so this
            # is empty on a normal run rather than being filtered here.
            artifacts=result.screenshots,
        )


#: A whole app's aria tree is ~2KB; a documentation page can be far more. This is generous for
#: the first and a hard stop for the second — a tool result is context, paid on every later turn.
MAX_SNAPSHOT_CHARS = 8000


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
    if obs and obs.snapshot:
        # WHAT IS ON SCREEN, with roles and states. This is the evidence — it is how you check
        # that the control you built exists and is enabled, and it is what to drive next: the
        # `steps` targets are these accessible names.
        extra.append("on screen:\n" + _clip(obs.snapshot))
    if result.screenshots:
        extra.append(
            "screenshot: "
            + ", ".join(result.screenshots)
            + "\nLook at it — you asked for it because the tree above cannot answer how "
            "something LOOKS, and that is the question it answers."
        )
    return "\n\n".join([head, body, *extra])


def _clip(snapshot: str) -> str:
    """The tree is small for an app and large for a document. Capped, and SAID when capped —
    a silently truncated structure reads as a page that ends where the text stops."""
    if len(snapshot) <= MAX_SNAPSHOT_CHARS:
        return snapshot
    return (
        snapshot[:MAX_SNAPSHOT_CHARS]
        + f"\n… (+{len(snapshot) - MAX_SNAPSHOT_CHARS} more characters of tree, not shown)"
    )

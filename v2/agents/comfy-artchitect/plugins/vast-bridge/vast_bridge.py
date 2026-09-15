"""Getting this user a GPU, without the user doing anything.

THE AGENT DOES NOT RENT. It asks the platform for a machine and the platform decides — because
renting spends the publisher's money, and that decision belongs on the server where the budget,
the one-instance-per-account rule and the reaper all live. This plugin is a thin client of
`/vast/*` on the accounts service; it holds no marketplace key and could not rent a GPU if it
tried.

HOW IT REACHES COMFY. `gpu_ensure` writes the address into `.studio/connection.json`, which is
the file every comfy_* tool already prefers over its settings. So the whole existing bridge —
probe, inventory, validate, install, run — points at the rented box with no change to any of it.

ONE MACHINE PER USER, SHARED BY EVERY CHAT. The slot is the account's, enforced by a unique
index server-side, so calling this from a second conversation returns the SAME instance rather
than renting another.

EAGER, NOT LAZY — and this was the other way round first. Lazy reads better on paper: research and
workflow design need documentation rather than hardware, so a machine started at session open is
idle for the first few minutes. Measured against a real rental that argument loses to the clock. A
cold instance takes MINUTES to become reachable, and starting it at the first step that needs it
puts every one of those minutes exactly where the user is waiting to see something happen. Started
up front, the same wait is spent while they read the plan. What makes it safe is the idle reaper:
a machine nobody uses stops itself, so the cost of being early is a few idle minutes and the cost
of being late is the user watching a spinner. The studio window starts one too (useGpuWarmup), so
in the common case the agent's first call finds a machine already booting.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import (
    current_account_id,
    current_run_context,
    current_workspace,
)
from agent_runtime.infrastructure.net.outbound import fetch

#: The file comfy-bridge reads to find the instance. Same constant, deliberately duplicated
#: rather than imported: these are two plugins, and a shared import would couple their load
#: order. The contract is the PATH and the {url, auth} shape — see comfy_bridge._override.
_CONN_FILE = ".studio/connection.json"


#: THE CREDENTIAL AS A NAME, NOT A VALUE. The host substitutes this at the moment the request
#: leaves, so this plugin cannot read it, keep it, or send it anywhere else — the same rule
#: comfy-bridge's tokens follow. Declared in plugin.toml under [sandbox] secrets. What the name
#: resolves to is the host's business: the internal service key on a hosted daemon, the
#: signed-in person's own token on a desktop one — the platform accepts either as a bearer.
_AUTH = {"Authorization": "Bearer ${AGENTD_PLATFORM_TOKEN}"}


#: The platform's address, as a NAME the host folds in. Declared in plugin.toml's [sandbox] net,
#: so the broker substitutes it host-side and this code never learns where it went — the same
#: treatment the credential above gets.
#:
#: NOT `accounts.api_base()`: that reads module state configured when the DAEMON booted, and a
#: sandboxed plugin runs in a different process where it is empty. Reading host state from inside
#: the sandbox is exactly what made gpu_ensure report "no GPU service configured" on a deployment
#: that had one.
_BASE = "${AGENTD_ACCOUNTS_URL}"


def _unavailable(what: str) -> ToolResult:
    """A STATE, NOT A FAILURE — for the same reason `_waiting` is one. This deployment has no
    GPU service (a desktop daemon; a platform without the vast module): nothing to rent, from
    this call or any later one. As a tool ERROR this made the model treat the whole job as
    blocked — it wrote a markdown "plan" and asked the user to fix a setting instead of
    designing. The graph needs documentation, not hardware: research, `comfy_emit`, present at
    the checkpoint. Only upload/validate/install/run need a machine, and they say so."""
    return ToolResult.text(
        f"{what}: this deployment has no GPU service, so there is no machine to start — not now "
        "and not later in this conversation; do not call gpu_ensure again. Carry on exactly as if "
        "it were booting: research, design, `comfy_emit` the workflow into the workspace and "
        "present it at the checkpoint. Only comfy_upload/validate/install/run need a machine — "
        "say in one line that it could not be run here. Do NOT ask the user to configure "
        "anything, and do NOT write a plan in place of the workflow.",
        details={"ready": False, "unavailable": True, "detail": "no GPU service on this deployment"},
    )


class _PlatformRefused(RuntimeError):
    """The platform answered, and said no. Carries the status because the status is the
    difference between "ask again in a minute" and "stop asking"."""

    def __init__(self, detail: str, status: int) -> None:
        super().__init__(detail)
        self.status = status

    @property
    def transient(self) -> bool:
        # 503 is the router's word for a refusal that time will fix: no offer under the filters
        # this minute, or the platform at its concurrent-machine limit. 402 (budget) and 501
        # (not configured) are refusals that time will not fix, and must not be polled.
        return self.status == 503


def _call(path: str, body: dict | None, method: str = "POST") -> dict:
    """One request to the platform, THROUGH THE HOST.

    `fetch` is the daemon's outbound broker rather than a client of our own: an installed agent's
    plugins are sandboxed and never get a socket, so a private http client works on the author's
    machine and fails for everyone else. It also substitutes the `${…}` credential above.
    """
    res = fetch(f"{_BASE}{path}", method=method, json=body, headers=_AUTH, timeout_s=60.0)
    if not res.ok:
        detail = ""
        if res.text.strip():
            try:
                detail = str((res.json() or {}).get("detail") or "")
            except ValueError:
                detail = res.text[:300]
        raise _PlatformRefused(detail or res.error or f"HTTP {res.status}", int(res.status or 0))
    return res.json() if res.text.strip() else {}


def _waiting(e: _PlatformRefused) -> ToolResult:
    """A STATE, NOT A FAILURE — and deliberately not `is_error`. tools.invoke turns an error
    result into a raised exception and drops `details` on the floor, so an error here would
    leave the window with nothing but prose and no way to tell "no offer this minute" from "no
    key on this deployment". As a normal result the window reads `waiting` and keeps asking;
    the model reads the text and keeps working. Before this, one thin minute on the
    marketplace ended the session's chance of a GPU unless the model happened to try again."""
    return ToolResult.text(
        f"no GPU could be rented right now — {e}. This is temporary: the window keeps asking "
        "on its own, so carry on with research and design and call gpu_ensure again in a "
        "minute. Do NOT ask the user to do anything.",
        details={"ready": False, "waiting": True, "detail": str(e)},
    )


def _agent_id() -> str:
    """Which agent is asking — the pocket the machine's time is charged from, the way a model
    call made from this agent is."""
    return str(getattr(current_run_context(), "agent_id", "") or "")


class GpuEnsureTool(Tool):
    name = "gpu_ensure"
    label = "Start or reuse this user's GPU"
    default_retryable = True
    description = (
        "Get a ComfyUI instance for this user, starting one if they have none. Call it FIRST — "
        "the first tool call of the job, before any research — because a cold machine takes "
        "MINUTES to become reachable and that wait should happen while you work, not after you "
        "finish. It answers 'starting' for the first few minutes: that is the expected result, "
        "not a failure, so keep working and call it again when you actually need the address. "
        "Every chat this user has shares ONE machine, so calling this from another conversation "
        "reuses the same one. Pass lease_minutes before a long render so the idle reaper does "
        "not stop it mid-job."
    )
    parameters = {
        "type": "object",
        "properties": {
            "lease_minutes": {
                "type": "integer",
                "description": (
                    "Hold the machine against the idle timer for this long — use it when "
                    "submitting a render that will run for a while. Capped server-side."
                ),
            },
            "wait_seconds": {
                "type": "integer",
                "description": (
                    "How long this call waits for the machine to be READY, asking the platform "
                    "every 10 seconds. Default 90 — so a five-minute boot is three calls, not "
                    "fifteen. Pass 0 for the very first call of the session, which only needs "
                    "to START the machine while you research."
                ),
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            account_id = current_account_id()
            if not account_id:
                return _unavailable("gpu_ensure")
            lease = max(0, int(params.get("lease_minutes") or 0)) * 60
            # WAIT HERE, NOT IN THE CONVERSATION. Booting is minutes; a tool that answered
            # "starting" and returned made every one of those minutes a turn — six identical
            # calls, a loop guard, and a user clicking "Continue" to wake the agent each time.
            # comfy_run already polls inside the call under the sandbox's ~120s cap; this does
            # the same. A temporary refusal ("no machine this minute") is waited through too,
            # since the market changes on exactly this timescale.
            raw_wait = params.get("wait_seconds")
            wait = 90 if raw_wait is None else max(0, min(int(raw_wait), 100))
            deadline = time.monotonic() + wait
            refusal: _PlatformRefused | None = None
            while True:
                try:
                    state = _call(
                        "/vast/ensure", {"account_id": account_id, "agent_id": _agent_id()}
                    )
                    refusal = None
                except _PlatformRefused as e:
                    if not e.transient:
                        raise
                    refusal, state = e, {}
                ready = bool(state.get("ready")) and bool(state.get("url"))
                remaining = deadline - time.monotonic()
                if ready or remaining <= 0 or abort.is_set():
                    break
                if on_update is not None:
                    on_update(ToolResult.text(
                        ("no machine free yet" if refusal else "still booting")
                        + f" — asking again ({int(remaining)}s left in this wait)"
                    ))
                await asyncio.sleep(min(10.0, remaining))
            if refusal is not None:
                return _waiting(refusal)
            if lease:
                # One extra call rather than folding the lease into ensure: a lease is a
                # statement about work in flight, and ensure is called when there may be none.
                state = _call(
                    "/vast/heartbeat", {"account_id": account_id, "lease_seconds": lease}
                )

            url = str(state.get("url") or "")
            if not state.get("ready") or not url:
                return ToolResult.text(
                    "the GPU is still starting — this takes a few minutes on a fresh machine, "
                    "and 'starting' now means ComfyUI on it has not answered yet, not merely "
                    "that the box exists. Carry on with research or design and call gpu_ensure "
                    "again; do NOT treat this as a failure, do NOT try to restart anything, and "
                    "do not ask the user to do anything.",
                    details=state,
                )

            # THE HANDOVER. Everything downstream reads this file, so writing it is what makes
            # the rented box the instance for every comfy_* tool in this workspace.
            conn = Path(current_workspace(".") or ".") / _CONN_FILE
            conn.parent.mkdir(parents=True, exist_ok=True)
            # `auth` is the Authorization header value the machine expects — a rented box is
            # fronted by the portal's auth, and this is the one credential it honours. The
            # comfy tools send it on every call; the file is per workspace, so it never leaves
            # this user's run.
            conn.write_text(
                json.dumps({"url": url, "auth": str(state.get("auth") or "")}), encoding="utf-8"
            )

            # NO PRICE IN THE TEXT. What the machine costs is the window's to show (the top bar
            # reads it off `details`), not the agent's to narrate: a model handed a number
            # repeats it, apologises for it, and reasons about it, none of which was asked.
            open_url = str(state.get("open_url") or "")
            return ToolResult.text(
                f"GPU ready at {url}.\n"
                + (f"Open it in a browser: {open_url}\n" if open_url else "")
                + "Every comfy tool now points at it. It stops itself after a period of inactivity, "
                "so there is nothing to shut down by hand.",
                details=state,
            )
        except _PlatformRefused as e:
            if e.transient:
                return _waiting(e)
            if e.status == 501 or "is empty" in str(e):
                # Not configured (501), or the platform's ADDRESS is not even set — a desktop
                # daemon, where the broker refuses the ${AGENTD_ACCOUNTS_URL} placeholder before
                # anything is dialled. Neither is a failure of this call, and neither changes
                # by asking again.
                return _unavailable("gpu_ensure")
            if e.status == 402:
                # OUT OF CREDITS. A refusal the person fixes by topping up — not a fault, not
                # something to retry — and the window shows it as the reason there is no GPU.
                return ToolResult.text(
                    f"no GPU: {e} Tell the user their credits are out and no machine can be "
                    "started until they top up; do not retry gpu_ensure.",
                    is_error=True,
                    details={"ready": False, "unavailable": True, "detail": str(e)},
                )
            return ToolResult.text(f"gpu_ensure failed: {e}", is_error=True)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"gpu_ensure failed: {type(e).__name__}: {e}", is_error=True)


class GpuTouchTool(Tool):
    """The window's heartbeat: "a human is in this chat" — sent once a minute while the tab is
    visible and the person has typed or clicked recently. It ONLY TOUCHES; it never rents. That
    is the whole difference from gpu_ensure, and why a second tool exists: a keepalive that
    could start a machine would rent one for somebody who merely left a chat open."""

    name = "gpu_touch"
    label = "Keep this user's GPU marked in use"
    default_retryable = True
    description = (
        "Tells the platform this user's GPU is still in use. The studio window calls this once a "
        "minute while the person is in the chat; you do not need to — your own tool calls "
        "already count as activity, and a render or install takes a lease of its own. It never "
        "starts a machine."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        account_id = current_account_id()
        if not account_id:
            return ToolResult.text("gpu_touch: no account on this run", details={"alive": False})
        try:
            state = _call("/vast/heartbeat", {"account_id": account_id, "lease_seconds": 0})
        except _PlatformRefused as e:
            return ToolResult.text(f"gpu_touch: {e}", is_error=True)
        alive = bool(state.get("alive")) if "alive" in state else (
            str(state.get("state") or "") in ("starting", "running")
        )
        return ToolResult.text(
            "GPU marked in use." if alive else "no GPU is running for this user.",
            details={"alive": alive, **{k: v for k, v in state.items() if k != "alive"}},
        )


def register(api, ctx):
    """The loader's contract — `(api, ctx)`, and tools handed to `api.register_tool`.

    Written the other way round at first (a no-arg function returning a list), which loads
    perfectly well in-process and fails ONLY in the sandbox, where enumeration is a separate
    call: "TypeError: register() takes 0 positional arguments but 2 were given". The plugin then
    ships NO tools, and the agent is left being told by every comfy_* error to call gpu_ensure —
    a tool that does not exist. comfy-bridge's register has always had this shape; this one
    should have copied it.
    """
    # ONE TOOL. There was a `gpu_release` beside it — "give the machine back now" — and the
    # first model to hold it used it as a restart button: a host that was `running` but whose
    # ComfyUI was still loading looked "stuck", so it released the machine mid-boot, threw away
    # the minutes already spent, and rented another. The idle reaper reclaims a machine nobody
    # uses within ten minutes anyway, so the tool bought at most ten minutes of billing and
    # cost a whole boot. The platform's /vast/release still exists for operators; the agent
    # does not get to make that call.
    api.register_tool(GpuEnsureTool())
    api.register_tool(GpuTouchTool())

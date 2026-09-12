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

import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_account_id, current_workspace
from agent_runtime.infrastructure.net.outbound import fetch

#: The file comfy-bridge reads to find the instance. Same constant, deliberately duplicated
#: rather than imported: these are two plugins, and a shared import would couple their load
#: order. The contract is the PATH and the {url, auth} shape — see comfy_bridge._override.
_CONN_FILE = ".studio/connection.json"


#: THE CREDENTIAL AS A NAME, NOT A VALUE. The host substitutes this at the moment the request
#: leaves, so this plugin cannot read the key, keep it, or send it anywhere else — the same rule
#: comfy-bridge's tokens follow. Declared in plugin.toml under [sandbox] secrets.
_INTERNAL = {"X-Internal-Key": "${AGENTD_ACCOUNTS_INTERNAL_KEY}"}


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
    return ToolResult.text(
        f"{what}: this deployment has no GPU service configured, so there is nothing to rent. "
        "Design and emit the workflow anyway — it does not need hardware — and tell the user "
        "the instance could not be started.",
        is_error=True,
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
    res = fetch(f"{_BASE}{path}", method=method, json=body, headers=_INTERNAL, timeout_s=60.0)
    if not res.ok:
        detail = ""
        if res.text.strip():
            try:
                detail = str((res.json() or {}).get("detail") or "")
            except ValueError:
                detail = res.text[:300]
        raise _PlatformRefused(detail or res.error or f"HTTP {res.status}", int(res.status or 0))
    return res.json() if res.text.strip() else {}


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
            }
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            account_id = current_account_id()
            if not account_id:
                return _unavailable("gpu_ensure")
            lease = max(0, int(params.get("lease_minutes") or 0)) * 60

            state = _call("/vast/ensure", {"account_id": account_id})
            if lease:
                # One extra call rather than folding the lease into ensure: a lease is a
                # statement about work in flight, and ensure is called when there may be none.
                state = _call(
                    "/vast/heartbeat", {"account_id": account_id, "lease_seconds": lease}
                )

            url = str(state.get("url") or "")
            if not state.get("ready") or not url:
                return ToolResult.text(
                    "the GPU is still starting — this takes a few minutes on a fresh machine. "
                    "Carry on with research or design and call gpu_ensure again in a minute; do "
                    "NOT treat this as a failure and do not ask the user to do anything.",
                    details=state,
                )

            # THE HANDOVER. Everything downstream reads this file, so writing it is what makes
            # the rented box the instance for every comfy_* tool in this workspace.
            conn = Path(current_workspace(".") or ".") / _CONN_FILE
            conn.parent.mkdir(parents=True, exist_ok=True)
            conn.write_text(json.dumps({"url": url, "auth": ""}), encoding="utf-8")

            hourly = float(state.get("hourly_usd") or 0.0)
            return ToolResult.text(
                f"GPU ready at {url} (${hourly:.3f}/hr, billed to the platform, not the user).\n"
                "Every comfy tool now points at it. It stops itself after a period of inactivity, "
                "so there is nothing to shut down by hand.",
                details=state,
            )
        except _PlatformRefused as e:
            if e.transient:
                # A STATE, NOT A FAILURE — and deliberately not `is_error`. tools.invoke turns an
                # error result into a raised exception and drops `details` on the floor, so an
                # error here would leave the window with nothing but prose and no way to tell
                # "no offer this minute" from "no key on this deployment". As a normal result
                # the window reads `waiting` and keeps asking; the model reads the text and
                # keeps working. Before this, one thin minute on the marketplace ended the
                # session's chance of a GPU unless the model happened to try again.
                return ToolResult.text(
                    f"no GPU could be rented right now — {e}. This is temporary: the window "
                    "keeps asking on its own, so carry on with research and design and call "
                    "gpu_ensure again in a minute. Do NOT ask the user to do anything.",
                    details={"ready": False, "waiting": True, "detail": str(e)},
                )
            return ToolResult.text(f"gpu_ensure failed: {e}", is_error=True)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"gpu_ensure failed: {type(e).__name__}: {e}", is_error=True)


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

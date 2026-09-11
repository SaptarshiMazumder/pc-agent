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

LAZY: call it at the first step that genuinely needs a GPU — validate, install or run. Research
and workflow design need documentation, not hardware, and renting before then bills for every
chat someone opens and abandons.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace
from agent_runtime.infrastructure import accounts
from agent_runtime.infrastructure.net.outbound import fetch

#: The file comfy-bridge reads to find the instance. Same constant, deliberately duplicated
#: rather than imported: these are two plugins, and a shared import would couple their load
#: order. The contract is the PATH and the {url, auth} shape — see comfy_bridge._override.
_CONN_FILE = ".studio/connection.json"


#: THE CREDENTIAL AS A NAME, NOT A VALUE. The host substitutes this at the moment the request
#: leaves, so this plugin cannot read the key, keep it, or send it anywhere else — the same rule
#: comfy-bridge's tokens follow. Declared in plugin.toml under [sandbox] secrets.
_INTERNAL = {"X-Internal-Key": "${AGENTD_ACCOUNTS_INTERNAL_KEY}"}


def _base() -> str:
    """Where the accounts service is, from the DAEMON's configuration.

    Never from an agent setting: an agent that could choose this address could point its GPU
    requests — and the credential above — at a machine of its own choosing.
    """
    return (accounts.api_base() or "").rstrip("/")


def _unavailable(what: str) -> ToolResult:
    return ToolResult.text(
        f"{what}: this deployment has no GPU service configured, so there is nothing to rent. "
        "Design and emit the workflow anyway — it does not need hardware — and tell the user "
        "the instance could not be started.",
        is_error=True,
    )


def _call(path: str, body: dict | None, method: str = "POST") -> dict:
    """One request to the platform, THROUGH THE HOST.

    `fetch` is the daemon's outbound broker rather than a client of our own: an installed agent's
    plugins are sandboxed and never get a socket, so a private http client works on the author's
    machine and fails for everyone else. It also substitutes the `${…}` credential above.
    """
    base = _base()
    if not base:
        raise RuntimeError("no accounts service is configured on this daemon")
    res = fetch(f"{base}{path}", method=method, json=body, headers=_INTERNAL, timeout_s=60.0)
    if not res.ok:
        detail = ""
        if res.text.strip():
            try:
                detail = str((res.json() or {}).get("detail") or "")
            except ValueError:
                detail = res.text[:300]
        raise RuntimeError(detail or res.error or f"HTTP {res.status}")
    return res.json() if res.text.strip() else {}


class GpuEnsureTool(Tool):
    name = "gpu_ensure"
    label = "Start or reuse this user's GPU"
    default_retryable = True
    description = (
        "Get a ComfyUI instance for this user, starting one if they have none. Call it at the "
        "FIRST step that actually needs hardware — comfy_validate, comfy_install or comfy_run — "
        "never before, because research and comfy_emit need no GPU. Booting takes a few minutes: "
        "if it answers 'starting', keep working and call again. Every chat this user has shares "
        "ONE machine, so calling this from another conversation reuses the same one. Pass "
        "lease_minutes before a long render so the idle reaper does not stop it mid-job."
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
            account_id = accounts.account_id()
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
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"gpu_ensure failed: {type(e).__name__}: {e}", is_error=True)


class GpuReleaseTool(Tool):
    name = "gpu_release"
    label = "Give the GPU back"
    default_retryable = False
    description = (
        "Stop this user's GPU now instead of waiting for it to time out. Use it only when the "
        "user says they are finished, or when they ask for it — never routinely at the end of a "
        "job, because the next request would then pay the several-minute start-up again."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            account_id = accounts.account_id()
            if not account_id:
                return _unavailable("gpu_release")
            state = _call("/vast/release", {"account_id": account_id})
            # Drop the pointer too, or the comfy tools keep dialling an address that is gone and
            # report a connection error instead of "there is no instance".
            (Path(current_workspace(".") or ".") / _CONN_FILE).unlink(missing_ok=True)
            return ToolResult.text(
                "GPU released — billing has stopped."
                if state.get("released")
                else "there was no GPU running for this user.",
                details=state,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"gpu_release failed: {type(e).__name__}: {e}", is_error=True)


def register(api, ctx):
    """The loader's contract — `(api, ctx)`, and tools handed to `api.register_tool`.

    Written the other way round at first (a no-arg function returning a list), which loads
    perfectly well in-process and fails ONLY in the sandbox, where enumeration is a separate
    call: "TypeError: register() takes 0 positional arguments but 2 were given". The plugin then
    ships NO tools, and the agent is left being told by every comfy_* error to call gpu_ensure —
    a tool that does not exist. comfy-bridge's register has always had this shape; this one
    should have copied it.
    """
    api.register_tool(GpuEnsureTool())
    api.register_tool(GpuReleaseTool())

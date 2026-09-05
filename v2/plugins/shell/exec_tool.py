"""exec tool: run shell commands; process tool: manage background sessions.

Foreground: asyncio.create_subprocess_shell, stdout+stderr merged, timeout,
middle-truncated output. Background: ProcessRegistry hands out session ids the
`process` tool can poll/kill.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import uuid
from dataclasses import dataclass, field

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace
from agent_runtime.application.tool_models import tool_config

OUTPUT_CAP = 50_000


def shell_fence_refusal(config, what: str) -> ToolResult | None:
    """The tenant fence, shared by exec and process: a run that carries read_roots gets no shell
    surface — a subprocess sees whatever the daemon's OS user sees, so every fs fence would be
    decorative the moment it ran. Returns the refusal, or None when the shell may proceed.

    THE EXCEPTION IS THE BUILDER. Agents named in `plugins.shell.exec.trusted_agents` keep their
    shell even under a fence. The default is agent-builder, and that is not a hole in the fence:
    a `requires_local` agent reaches a hosted daemon ONLY through the operator's own
    `hosted_agents_allow` opt-in, so the shell rides consent that was already given explicitly —
    and the trust follows the RUN's agent_id, which a built agent cannot inherit."""
    from agent_runtime.application.run_context import current_run_context

    ctx = current_run_context()
    if ctx is None or not getattr(ctx, "read_roots", ()):
        return None
    trusted = tool_config(config, "shell", "exec", "trusted_agents", default=("agent-builder",))
    if str(getattr(ctx, "agent_id", "") or "") in tuple(trusted or ()):
        return None
    return ToolResult.text(
        f"{what} is not available on this server: a shell cannot be confined to your "
        "own files. Use the read/write/edit/ls/find tools instead.",
        is_error=True,
    )


def middle_truncate(text: str, cap: int = OUTPUT_CAP) -> str:
    if len(text) <= cap:
        return text
    half = cap // 2
    omitted = len(text) - cap
    return f"{text[:half]}\n... [{omitted} chars truncated] ...\n{text[-half:]}"


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            proc.send_signal(signal.SIGKILL)
    except ProcessLookupError:
        pass


# ---------------------------------------------------------------------------
# Background process registry
# ---------------------------------------------------------------------------


@dataclass
class BackgroundProcess:
    session_id: str
    command: str
    proc: asyncio.subprocess.Process
    output: list[bytes] = field(default_factory=list)
    reader_task: asyncio.Task | None = None
    read_cursor: int = 0

    @property
    def running(self) -> bool:
        return self.proc.returncode is None

    def drain_new_output(self) -> str:
        data = b"".join(self.output)
        new = data[self.read_cursor :]
        self.read_cursor = len(data)
        return new.decode("utf-8", errors="replace")


class ProcessRegistry:
    def __init__(self):
        self.sessions: dict[str, BackgroundProcess] = {}

    async def start(self, command: str, cwd: str, env: dict) -> str:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=env,
        )
        session_id = uuid.uuid4().hex[:8]
        bp = BackgroundProcess(session_id=session_id, command=command, proc=proc)

        async def pump():
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                bp.output.append(chunk)
            await proc.wait()

        bp.reader_task = asyncio.create_task(pump())
        self.sessions[session_id] = bp
        return session_id


_REGISTRY = ProcessRegistry()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class ExecTool(Tool):
    name = "exec"
    default_timeout_sec = None  # self-limits via exec_timeout_sec; no GuardedTool wrapper
    default_retryable = False
    description = (
        "Run a shell command and return its merged stdout+stderr with the exit code; long "
        "output is truncated in the middle. Set background=true to start a long-running "
        "command and get a session id back immediately, then use the `process` tool to poll "
        "its output or kill it. Best for commands, scripts, git, builds, and package "
        "managers — to read or change files, prefer the read/write/edit/ls/find tools, and "
        "do NOT use sleep/delay loops to schedule reminders or follow-ups."
    )
    label = "Exec"
    concurrency = "sequential"
    parameters = {
        "type": "object",
        "required": ["command"],
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute."},
            "cwd": {"type": "string", "description": "Working directory (default: workspace)."},
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Extra environment variables (merged).",
            },
            "timeout_sec": {"type": "integer", "minimum": 1, "description": "Timeout in seconds."},
            "background": {
                "type": "boolean",
                "description": "Run in background; returns a session id.",
            },
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        # The tenant fence, with the trusted-builder exception — see shell_fence_refusal. Not a
        # mode branch: the same rule as check_read, decided by the values the run carries.
        # Desktop runs carry none.
        refusal = shell_fence_refusal(self.config, "exec")
        if refusal is not None:
            return refusal
        command = params["command"]
        cwd = params.get("cwd") or current_workspace(str(self.config.workspace))
        env = {**os.environ, **(params.get("env") or {})}
        timeout = params.get("timeout_sec") or tool_config(
            self.config, "shell", "exec", "timeout_sec", default=1800
        )

        if params.get("background"):
            session_id = await _REGISTRY.start(command, cwd, env)
            return ToolResult.text(
                f"Started background session {session_id}. Use the process tool to poll it."
            )

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=env,
        )

        abort_task = asyncio.create_task(abort.wait())
        comm_task = asyncio.create_task(proc.communicate())
        try:
            done, _pending = await asyncio.wait(
                {comm_task, abort_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if comm_task in done:
                stdout, _ = comm_task.result()
                output = middle_truncate(stdout.decode("utf-8", errors="replace"))
                status = f"exit code {proc.returncode}"
                return ToolResult.text(
                    f"({status})\n{output}" if output.strip() else f"({status}) no output",
                    is_error=proc.returncode != 0,
                )
            # aborted or timed out
            await _kill_process(proc)
            comm_task.cancel()
            reason = "aborted" if abort.is_set() else f"timed out after {timeout}s"
            return ToolResult.text(f"Command {reason}: {command}", is_error=True)
        finally:
            abort_task.cancel()


class ProcessTool(Tool):
    name = "process"
    default_timeout_sec = None
    default_retryable = False
    description = (
        "Manage background `exec` sessions (those started with background=true). action=list "
        "shows each session's running/exited status; action=poll returns any new output and "
        "whether it is still running — use it to confirm a background command finished or to "
        "collect its logs; action=kill terminates it. poll/kill need the session_id returned "
        "by `exec`."
    )
    label = "Process"
    parameters = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["list", "poll", "kill"]},
            "session_id": {"type": "string", "description": "Session id (for poll/kill)."},
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        # Same fence as ExecTool (same helper, same trusted-agents exception), and for the same
        # reason: _REGISTRY is process-GLOBAL, so on a shared daemon action=list would spill
        # every tenant's command strings and output. Symmetric guards, not one depending on the
        # other.
        refusal = shell_fence_refusal(self.config, "process")
        if refusal is not None:
            return refusal
        action = params["action"]
        if action == "list":
            if not _REGISTRY.sessions:
                return ToolResult.text("No background sessions.")
            lines = [
                f"{bp.session_id}  {'running' if bp.running else f'exited({bp.proc.returncode})'}  {bp.command}"
                for bp in _REGISTRY.sessions.values()
            ]
            return ToolResult.text("\n".join(lines))

        session_id = params.get("session_id", "")
        bp = _REGISTRY.sessions.get(session_id)
        if bp is None:
            return ToolResult.text(f"Unknown session: {session_id}", is_error=True)

        if action == "poll":
            new = middle_truncate(bp.drain_new_output())
            status = "running" if bp.running else f"exited({bp.proc.returncode})"
            return ToolResult.text(f"[{status}]\n{new}" if new else f"[{status}] no new output")

        if action == "kill":
            await _kill_process(bp.proc)
            return ToolResult.text(f"Killed session {session_id}.")

        return ToolResult.text(f"Unknown action: {action}", is_error=True)

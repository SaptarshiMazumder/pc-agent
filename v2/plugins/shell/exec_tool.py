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


def shell_route(config, what: str) -> tuple[str, ToolResult | None]:
    """WHERE this shell call may run: ("local", None), ("microvm", None), or ("", refusal).

    No fence (desktop) -> local, unchanged. A run that carries a tenant fence (read_roots) NEVER
    gets a local shell — a subprocess sees whatever the daemon's OS user sees, so every fs fence
    would be decorative the moment it ran, and on a hosted daemon the rule is stricter still:
    nothing the user directs executes on the box that holds every tenant's data.

    Agents named in `plugins.shell.exec.trusted_agents` (default: agent-builder — which only
    reaches a hosted daemon through the operator's own `hosted_agents_allow` opt-in, and whose
    run identity a built agent cannot inherit) get the MICROVM shell instead: the command runs
    in the executor service's Firecracker microVM with the run's workspace synced through
    (sandbox/microvm_backend.run_shell). Everyone else — and a trusted agent on a daemon with no
    executor configured — is refused."""
    from agent_runtime.application.run_context import current_run_context

    ctx = current_run_context()
    if ctx is None or not getattr(ctx, "read_roots", ()):
        return "local", None
    trusted = tool_config(config, "shell", "exec", "trusted_agents", default=("agent-builder",))
    if str(getattr(ctx, "agent_id", "") or "") in tuple(trusted or ()):
        if str(getattr(config, "executor_url", "") or "").strip():
            return "microvm", None
        return "", ToolResult.text(
            f"{what} cannot run here yet: this agent is shell-trusted, but the daemon has no "
            "executor service configured (AGENTD_EXECUTOR_URL) to run commands in a microVM — "
            "and on this server a shell never runs on the daemon's own box.",
            is_error=True,
        )
    return "", ToolResult.text(
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
        # WHERE may this run — see shell_route. Not a mode branch: the same rule as check_read,
        # decided by the values the run carries. Desktop runs carry none and stay local.
        route, refusal = shell_route(self.config, "exec")
        if refusal is not None:
            return refusal
        command = params["command"]
        cwd = params.get("cwd") or current_workspace(str(self.config.workspace))
        env = {**os.environ, **(params.get("env") or {})}
        timeout = params.get("timeout_sec") or tool_config(
            self.config, "shell", "exec", "timeout_sec", default=1800
        )

        if route == "microvm":
            return await self._execute_microvm(command, cwd, params, float(timeout))

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

    async def _execute_microvm(self, command: str, cwd: str, params: dict,
                               timeout: float) -> ToolResult:
        """The fenced branch: the command runs in the executor's microVM with THIS run's
        workspace synced through — never on the daemon's own box. Same result shape as the
        local path, plus a note naming where it ran (so 'why can't I see /etc' is answerable).
        Background sessions don't exist there: the microVM lives exactly as long as the call."""
        if params.get("background"):
            return ToolResult.text(
                "background sessions are not available on this server — the microVM lives "
                "exactly as long as one command. Run it in the foreground (raise timeout_sec "
                "if it is long).",
                is_error=True,
            )
        from agent_runtime.infrastructure.tools.sandbox.microvm_backend import (
            ExecutorError,
            OversizeError,
            run_shell,
        )

        try:
            ok, output, meta = await run_shell(
                self.config, command, cwd, timeout, env=params.get("env") or {}
            )
        except (ExecutorError, OversizeError) as e:
            return ToolResult.text(
                f"the microVM shell could not run this command: {e}\n(This is the environment "
                "failing, not your command — the executor service was unreachable or refused.)",
                is_error=True,
            )
        status = f"exit code {meta.get('exit_code')}"
        body = middle_truncate(output)
        return ToolResult.text(
            f"(microVM · {status})\n{body}" if body.strip() else f"(microVM · {status}) no output",
            is_error=not ok,
        )


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
        # Same routing as ExecTool, and for the same reason: _REGISTRY is process-GLOBAL, so on
        # a shared daemon action=list would spill every tenant's command strings and output.
        # There is no microvm branch to take here — background sessions do not exist off-box
        # (the microVM lives exactly as long as one command) — so a fenced-but-trusted agent
        # gets the honest answer instead of an empty registry pretending to be one.
        route, refusal = shell_route(self.config, "process")
        if refusal is not None:
            return refusal
        if route == "microvm":
            return ToolResult.text(
                "No background sessions: on this server the shell runs in a per-command microVM, "
                "which lives exactly as long as the command — nothing persists to manage."
            )
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

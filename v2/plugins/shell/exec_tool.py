"""exec tool: run shell commands; process tool: manage background sessions.

Foreground: asyncio.create_subprocess_shell, stdout+stderr merged, timeout,
middle-truncated output. Background: ProcessRegistry hands out session ids the
`process` tool can poll/kill. On a hosted daemon, where each runs is decided by
shell_route below.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
import uuid
from pathlib import Path
from dataclasses import dataclass, field

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import (
    current_run_context,
    current_setting_value,
    current_workspace,
)
from agent_runtime.application.tool_models import tool_config
from agent_runtime.domain.agentd_ignore import FILENAME as IGNORE_FILENAME
from agent_runtime.domain.agentd_ignore import AgentdIgnore
from agent_runtime.infrastructure import accounts, user_state
from agent_runtime.infrastructure.tools.sandbox.confined_command import ConfinedCommand
from agent_runtime.infrastructure.tools.sandbox.microvm_backend import (
    AUTHORING_SKIP_DIRS,
    WORKSPACE_SKIP_DIRS,
    ExecutorError,
    OversizeError,
    run_shell,
)
from agent_runtime.infrastructure.tools.sandbox.landlock_confinement import (
    LandlockConfinement,
    LandlockUnavailable,
)

OUTPUT_CAP = 50_000


def command_env(params: dict) -> dict[str, str]:
    """What a command gets on top of its base environment: THIS AGENT'S OWN declared settings,
    then the `env` the model passed.

    WHY SETTINGS ARE INJECTED. A CLI reads its credentials from the environment (`terraform` and
    `aws` look for AWS_ACCESS_KEY_ID), and the model never sees a secret's value, so it cannot
    pass one. Without this a hosted command ran with no credentials at all, and a desktop one
    found nothing either: settings are stored under the agent-prefixed name, not the bare one the
    CLI reads. So each declared setting goes in under its own name, resolved through
    current_setting_value — the same single door `fetch`, the plugin sandbox and MCP use, so the
    account's own value wins, an unset one stays unset rather than falling back to the server's
    credential, and a name the agent never declared is never injected.

    `${NAME}` in the model's env values resolves the same way, so `"${AWS_REGION}"` works; an
    unknown name is left as literal text rather than blanked."""
    from agent_runtime.domain.sandbox_net import substitute

    ctx = current_run_context()
    declared = tuple(getattr(ctx, "settings", ()) or ()) if ctx is not None else ()
    settings = {name: current_setting_value(name) for name in declared}
    settings = {k: v for k, v in settings.items() if v}
    extra = {str(k): substitute(str(v), settings) for k, v in (params.get("env") or {}).items()}
    return {**settings, **extra}


def _is_under(path: str, root: str) -> bool:
    """Is `path` inside `root`, after resolving both? The same containment the fs tools use."""
    try:
        p, r = os.path.realpath(path), os.path.realpath(root)
        return os.path.commonpath([p, r]) == r
    except ValueError:  # different drives on Windows
        return False


def shell_route(config, what: str, background: bool = False) -> tuple[str, ToolResult | None]:
    """WHERE this shell call runs: ("local"|"microvm"|"confined", None), or ("", refusal).

    No fence (desktop) -> "local", unchanged: one person, their own machine.

    A run that carries a tenant fence (read_roots — every run on a hosted daemon) never gets an
    unconfined shell, because a plain subprocess sees whatever the daemon sees: every account's
    files. There is no list of agents allowed a shell; WHERE a command runs is what makes it
    safe, so every agent gets the same answer:

      * FOREGROUND -> "microvm". The executor's Firecracker microVM, with the account's own
        files synced through (sandbox/microvm_backend.run_shell). Nothing runs on the daemon.
      * BACKGROUND -> "confined". A microVM lives exactly as long as one call, so a command
        meant to keep running cannot live there. It runs on the daemon instead, locked to the
        run's own roots (sandbox/confined_command.py) — the same boundary the fs tools hold.

    A foreground command on a daemon with no executor configured is refused: there is nowhere
    safe to put it."""

    ctx = current_run_context()
    if ctx is None or not getattr(ctx, "read_roots", ()):
        return "local", None
    if background:
        return "confined", None
    if str(getattr(config, "executor_url", "") or "").strip():
        return "microvm", None
    return "", ToolResult.text(
        f"{what} cannot run here: this server has no executor service configured "
        "(AGENTD_EXECUTOR_URL), and a foreground command never runs on the daemon's own box. "
        "Run it with background=true to use the confined shell instead.",
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
    #: WHO started it — the account id on a hosted daemon, "" on desktop. The registry is one
    #: per daemon, and a hosted daemon serves many accounts: without this, `process list` would
    #: print every account's commands and `poll` would read anyone's output.
    owner: str = ""
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

    async def start(self, command: str, cwd: str, env: dict, owner: str = "",
                    confined: ConfinedCommand | None = None) -> str:
        """Start a background command. `confined` given -> it runs inside that confinement
        (hosted); absent -> a plain shell (desktop)."""
        if confined is None:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=env,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *confined.argv(command),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=env,
            )
        session_id = uuid.uuid4().hex[:8]
        bp = BackgroundProcess(session_id=session_id, command=command, proc=proc, owner=owner)

        async def pump():
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                bp.output.append(chunk)
            await proc.wait()
            if confined is not None:
                confined.discard_scratch()

        bp.reader_task = asyncio.create_task(pump())
        self.sessions[session_id] = bp
        return session_id

    def owned_by(self, owner: str) -> list[BackgroundProcess]:
        return [bp for bp in self.sessions.values() if bp.owner == owner]

    def get(self, session_id: str, owner: str) -> BackgroundProcess | None:
        """A session, only if `owner` started it. Another account's id answers exactly like a
        made-up one, so the reply does not confirm that someone else's session exists."""
        bp = self.sessions.get(session_id)
        return bp if bp is not None and bp.owner == owner else None


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
        route, refusal = shell_route(self.config, "exec", background=bool(params.get("background")))
        if refusal is not None:
            return refusal
        command = params["command"]
        cwd = params.get("cwd") or current_workspace(str(self.config.workspace))
        # The machine's environment, minus the names this agent declared: an unset setting
        # must stay unset, never quietly pick up whatever the machine exports under that name.
        declared = set(getattr(current_run_context(), "settings", ()) or ())
        env = {**{k: v for k, v in os.environ.items() if k not in declared}, **command_env(params)}
        timeout = params.get("timeout_sec") or tool_config(
            self.config, "shell", "exec", "timeout_sec", default=1800
        )

        if route == "microvm":
            return await self._execute_microvm(command, cwd, params, float(timeout))
        if route == "confined":
            return await self._start_confined(command, cwd, params)

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
        """The fenced branch: the command runs in the executor's microVM - never on the daemon's
        own box - with the caller's OWN FILES synced through and the changes applied back.

        WHAT IT CAN SEE is the account's agent tree, not just the run's workspace. The builder
        edits agents, so the file it wrote with `write` a moment ago is the file its next
        `python -c compile(...)` has to open; syncing only the workspace gave it ENOENT for
        files `ls` had just listed, and it wasted a third of a build working around that.
        Everything derived is left behind (ui/, sessions/, workspace/, node_modules) - measured
        on staging, that is 37 MB down to 1.7 MB.

        Only foreground commands arrive here: a microVM lives exactly as long as one call, so
        shell_route sends background ones to the confined shell instead.
        """
        sync_root, skip_dirs, ignore = self._sync_tree(cwd)
        try:
            ok, output, meta = await run_shell(
                self.config, command, cwd, timeout,
                env=command_env(params), sync_root=sync_root, skip_dirs=skip_dirs,
                ignore=ignore,
            )
        except (ExecutorError, OversizeError) as e:
            return ToolResult.text(
                f"the microVM shell could not run this command: {e}"
                + chr(10)
                + "(This is the environment failing, not your command - the executor was "
                "unreachable, or it refused to apply what the command wrote.)",
                is_error=True,
            )
        status = f"exit code {meta.get('exit_code')}"
        applied = meta.get("applied") or []
        head = f"(microVM - {status}"
        head += f" - applied {len(applied)} file(s))" if applied else ")"
        body = middle_truncate(output)
        return ToolResult.text(
            f"{head}{chr(10)}{body}" if body.strip() else f"{head} no output",
            is_error=not ok,
        )

    async def _start_confined(self, command: str, cwd: str, params: dict) -> ToolResult:
        """The hosted background branch: a command on the daemon's own box, locked to the run's
        own roots (sandbox/confined_command.py). Refused — never run unconfined — when the lock
        cannot form."""

        ctx = current_run_context()
        write_roots = tuple(getattr(ctx, "write_clamp", ()) or ())
        if not any(_is_under(cwd, root) for root in write_roots):
            return ToolResult.text(
                f"cannot start a background command in {cwd}: it is outside your own files.",
                is_error=True,
            )
        try:
            LandlockConfinement.abi_version()
        except LandlockUnavailable as e:
            return ToolResult.text(
                f"background commands are unavailable on this server: {e}. (This is the "
                "environment, not your command.)",
                is_error=True,
            )
        confined = ConfinedCommand(
            read_roots=tuple(getattr(ctx, "read_roots", ()) or ()),
            write_roots=write_roots,
            scratch_dir=tempfile.mkdtemp(prefix="agentd-bg-"),
        )
        session_id = await _REGISTRY.start(
            command, cwd,
            confined.environment(home=cwd, extra=command_env(params)),
            owner=str(getattr(ctx, "account_id", "") or ""),
            confined=confined,
        )
        return ToolResult.text(
            f"Started background session {session_id}. Use the process tool to poll it."
        )

    def _sync_tree(self, cwd: str) -> tuple[str, frozenset, AgentdIgnore]:
        """WHICH tree travels into the box, what it leaves behind, and what the agent declared
        must never travel: WHAT THE COMMAND MAY CHANGE.

        An agent that DECLARES a write scope beyond its own folder (`[tools.fs] write_roots` —
        in practice the builder, whose job is editing other agents) gets the account's whole
        agents tree, minus every agent's workspace and build output: the file it just wrote with
        `write` is the one its next compile check must open. Each agent folder's own
        `.agentdignore` applies inside that folder, the way nested .gitignore files do. Every
        other agent gets its own working directory, whole, with its definition's `.agentdignore`
        applied to it — that is where its files, state and project live, and where its commands
        leave their downloads and caches.

        Read from the account the run is pinned to, so it is the same tenancy every other write
        on this run resolves against - never a wider root."""
        ctx = current_run_context()
        acct = accounts.account_id()
        if ctx is not None and getattr(ctx, "write_roots", ()) and acct:
            root = Path(user_state.account_agents_dir(self.config.state_dir, acct))
            if root.is_dir():
                files = [
                    (f.parent.name, f.read_text(encoding="utf-8", errors="replace"))
                    for f in sorted(root.glob(f"*/{IGNORE_FILENAME}"))
                ]
                return str(root), AUTHORING_SKIP_DIRS, AgentdIgnore.from_files(files)
        own = Path(str(getattr(ctx, "agent_dir", "") or "")) / IGNORE_FILENAME if ctx else None
        files = (
            [("", own.read_text(encoding="utf-8", errors="replace"))]
            if own is not None and own.is_file()
            else []
        )
        return cwd, WORKSPACE_SKIP_DIRS, AgentdIgnore.from_files(files)


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
        # _REGISTRY is one per daemon, so every read here is filtered to the caller's own
        # sessions: on a hosted daemon, list/poll/kill must never reach another account's
        # command, output or process. Desktop sessions are all owned by "" — one person.

        ctx = current_run_context()
        owner = str(getattr(ctx, "account_id", "") or "") if ctx is not None else ""
        action = params["action"]
        if action == "list":
            mine = _REGISTRY.owned_by(owner)
            if not mine:
                return ToolResult.text("No background sessions.")
            lines = [
                f"{bp.session_id}  {'running' if bp.running else f'exited({bp.proc.returncode})'}  {bp.command}"
                for bp in mine
            ]
            return ToolResult.text("\n".join(lines))

        session_id = params.get("session_id", "")
        bp = _REGISTRY.get(session_id, owner)
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

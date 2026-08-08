"""SubprocessPluginSandbox — the first PluginSandbox backend with a real boundary in it.

One tool call = one short-lived child process, then the process is gone. What crosses the line is
JSON in and JSON out; what does NOT cross is everything else the daemon is holding.

WHAT THE CHILD DOES NOT GET, which is the whole point and is worth being concrete about:

  * no runtime handles — browser, credential vault, task ledger, memory bank, agent registry are
    all `None` in its PluginContext. Not filtered: absent. There is no object to reach.
  * no environment — the env is built from an allowlist, so the daemon's provider keys, AWS
    credentials and internal service tokens are simply not there.
  * no Config — a redacted projection instead (protocol.config_projection), so the plugin cannot
    read keys out of the config object either.
  * no inherited descriptors, no shared memory, no interpreter state, and no way to keep running
    after the call: the parent kills the process on timeout or abort.

Inside the child, `child_guard` polices what remains: filesystem reads/writes outside the grant,
network, spawning helpers, ctypes. Read that module's header for exactly how far that goes and
where it stops — the honest summary is that it is an interpreter-level control, not a kernel one,
and `sandbox_child_uid` (POSIX, daemon running as root) is what turns it into a kernel one.

MODEL CALLS ARE INVERTED, not excepted. A tool with no network and no credentials cannot call a
model, and most plugins worth installing want to. Rather than punch a hole in the grant, the child
ASKS: it sends a `model_request` frame and this process makes the call, checks it against the
grant, clamps it and meters it against the account running the agent (`model_broker.py`). So the
channel here is bidirectional — the child's stdin stays open for the answers.

COST. A child process per tool call is roughly 40-120ms of interpreter start plus the plugin's own
import time. That is the trade: it is charged only on the UNTRUSTED tier (a marketplace agent's
own plugins), never on built-ins, and `outcome=timeout` plus the elapsed timing are emitted so the
overhead is measurable rather than assumed. If it doubles a real workload's latency, the config
knob to fall back to `local` is one line — but so is the reason not to.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from agent_runtime.application.interfaces.tool import OnUpdate, Tool, ToolResult
from agent_runtime.application.run_context import RunContext
from agent_runtime.domain.sandbox import CapabilityGrant
from agent_runtime.infrastructure import telemetry
from agent_runtime.infrastructure.tools.sandbox import protocol, stdout_capture
from agent_runtime.infrastructure.tools.sandbox.model_broker import SandboxModelBroker

log = logging.getLogger("agentd")

#: Environment variables the child inherits. A SEED — `config.sandbox_env_passthrough` overrides
#: it. Nothing here identifies the operator or unlocks anything; these are the variables without
#: which the interpreter cannot start (Windows in particular refuses without SYSTEMROOT).
DEFAULT_ENV_PASSTHROUGH: tuple[str, ...] = (
    "PATH",
    "SYSTEMROOT",
    "SystemRoot",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "LANG",
    "LC_ALL",
    "TZ",
)

#: Wall clock for a call whose grant does not set one.
DEFAULT_TIMEOUT_S = 120.0

#: Max bytes in one protocol line. A tool returning a base64 image easily passes asyncio's 64KB
#: default, and the failure mode there is a ValueError mid-stream that looks like a protocol bug.
STREAM_LIMIT = 32 * 1024 * 1024

#: How often the watchdog checks the deadline. Fine-grained enough that an overrun is stopped
#: promptly, coarse enough that a mostly-idle daemon is not waking up constantly.
_DEADLINE_TICK_S = 0.5


class _Deadline:
    """The tool's own wall clock, which STOPS while the host is doing work on its behalf.

    A sandboxed tool has no network, so its model calls are served by us — and a 60s model call
    would otherwise eat a 120s tool budget that was never meant to cover it. Charging the child for
    time it spent blocked on the parent produces kills that look random and depend on how busy the
    model provider is, which is close to unreproducible. So the clock pauses for exactly the
    interval the child is waiting on us.

    Reentrant by depth, because several model requests can be in flight at once and the clock must
    not restart when the first of them finishes.
    """

    def __init__(self, timeout_s: float) -> None:
        self._timeout = timeout_s
        self._start = time.monotonic()
        self._paused_total = 0.0
        self._paused_at: float | None = None
        self._depth = 0

    def pause(self) -> None:
        self._depth += 1
        if self._depth == 1:
            self._paused_at = time.monotonic()

    def resume(self) -> None:
        self._depth = max(0, self._depth - 1)
        if self._depth == 0 and self._paused_at is not None:
            self._paused_total += time.monotonic() - self._paused_at
            self._paused_at = None

    def charged(self) -> float:
        """Seconds of the tool's OWN time used so far."""
        paused = self._paused_total
        if self._paused_at is not None:
            paused += time.monotonic() - self._paused_at
        return time.monotonic() - self._start - paused

    def expired(self) -> bool:
        return self._timeout > 0 and self.charged() > self._timeout


class SubprocessPluginSandbox:
    name = "subprocess"

    def __init__(self, config=None) -> None:
        self._config = config
        # (plugin_id, tool_name) -> how the CHILD reloads it: the module entry and the plugin's
        # own directory. The tool object itself never crosses; the child imports the code afresh,
        # exactly as a container backend would.
        self._plugins: dict[tuple[str, str], tuple[str, str]] = {}

    # ------------------------------------------------------------------ wiring

    def register(self, plugin_id: str, tool_name: str, tool: Tool) -> None:
        """Called by SandboxedTool. Records the tool's provenance so the child can load it.

        Same method name the in-process backend uses, so the wrapper does not know or care which
        backend it has. Here it records a recipe; there it records the object.
        """
        entry = getattr(tool, "_plugin_entry", "") or ""
        root = getattr(tool, "_plugin_root", "") or ""
        self._plugins[(plugin_id, tool_name)] = (str(entry), str(root))

    # ------------------------------------------------------------------ the call

    async def run_tool(
        self,
        plugin_id: str,
        tool_name: str,
        tool_call_id: str,
        params: dict,
        abort,
        on_update: OnUpdate | None = None,
        *,
        grant: CapabilityGrant,
        ctx: RunContext | None = None,
    ) -> ToolResult:
        entry, root = self._plugins.get((plugin_id, tool_name), ("", ""))
        if not entry:
            # Fail CLOSED and say why. Falling back to in-process would silently turn the sandbox
            # off for exactly the plugin whose provenance we could not establish.
            return self._fail(
                plugin_id,
                tool_name,
                "no-recipe",
                f"sandbox: cannot locate the code for '{tool_name}' (plugin '{plugin_id}'), so it "
                "will not be run. This tool was registered without its plugin entry/root.",
            )

        timeout = grant.timeout_s if grant.timeout_s > 0 else DEFAULT_TIMEOUT_S
        workspace = self._workspace(grant, ctx)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="agentd-sbx-") as temp_dir:
            job = {
                "plugin_id": plugin_id,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "entry": entry,
                "plugin_root": root,
                "params": params or {},
                "grant": protocol.grant_payload(grant),
                "ctx": protocol.ctx_payload(ctx),
                "config": protocol.config_projection(
                    self._config, plugin_id, self._setting("sandbox_config_fields", ())
                )
                if self._config is not None
                else {},
                "temp_dir": temp_dir,
                "deny_paths": self._deny_paths(),
                "allow_native": bool(self._setting("sandbox_allow_native", False)),
            }
            return await self._spawn_and_wait(
                job, grant, workspace, temp_dir, timeout, abort, on_update, started
            )

    async def _spawn_and_wait(
        self, job, grant, workspace, temp_dir, timeout, abort, on_update, started
    ) -> ToolResult:
        plugin_id, tool_name = job["plugin_id"], job["tool_name"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace or None,
                env=self._child_env(grant, workspace, temp_dir),
                limit=STREAM_LIMIT,
                **self._posix_hardening(grant),
            )
        except NotImplementedError:
            # Windows with a Selector event loop cannot spawn. Naming the fix beats a stack trace.
            return self._fail(
                plugin_id, tool_name, "no-subprocess",
                "sandbox: this event loop cannot start subprocesses. Set "
                "AGENTD_SANDBOX_BACKEND=local, or run the daemon on a Proactor loop (Windows).",
            )
        except OSError as e:
            return self._fail(plugin_id, tool_name, "spawn-failed", f"sandbox: cannot start the sandbox process: {e}")

        broker = SandboxModelBroker(
            self._config, plugin_id=plugin_id, tool_name=tool_name, grant=grant
        )
        # The deadline is on the TOOL's own time, not on ours. A model call the host is serving can
        # legitimately take a minute, and charging that to the tool's budget would kill healthy
        # runs under load — the kind of failure that looks random and never reproduces.
        deadline = _Deadline(timeout)
        killer = asyncio.create_task(self._kill_on_abort(abort, proc))
        watchdog = asyncio.create_task(self._watch_deadline(deadline, proc))
        try:
            payload, stderr_text = await self._collect(proc, job, on_update, broker, deadline)
        finally:
            for task in (killer, watchdog):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await self._terminate(proc)

        if deadline.expired():
            return self._fail(
                plugin_id, tool_name, "timeout",
                f"sandbox: '{tool_name}' exceeded its {timeout:.0f}s limit and was stopped."
                + (f" ({broker.calls_made} model call(s) were not counted against it.)"
                   if broker.calls_made else ""),
            )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if stderr_text.strip():
            # Same treatment the in-process backend gives a plugin's prints: scrubbed, capped, and
            # logged as the PLUGIN's output rather than merged into the daemon's own stream.
            self._log_child_output(plugin_id, tool_name, stderr_text)
        if payload is None:
            if abort is not None and abort.is_set():
                return self._fail(plugin_id, tool_name, "aborted", "sandbox: the run was cancelled.")
            return self._fail(
                plugin_id, tool_name, "no-result",
                f"sandbox: '{tool_name}' exited without returning a result "
                f"(code {proc.returncode}). Its output was logged.",
            )

        self._record_denials(plugin_id, tool_name, payload.get("denials") or [])
        outcome = "error" if payload.get("isError") else "ok"
        telemetry.count(
            "sandbox_run_total",
            source="sandbox",
            _props={"backend": self.name, "plugin_id": plugin_id, "tool": tool_name, "outcome": outcome},
        )
        telemetry.timing(
            "sandbox_run_ms", elapsed_ms, source="sandbox",
            _props={"backend": self.name, "plugin_id": plugin_id, "tool": tool_name},
        )
        return protocol.payload_result(payload)

    # ------------------------------------------------------------------ plumbing

    async def _collect(self, proc, job, on_update, broker, deadline) -> tuple[dict | None, str]:
        """Read the protocol from stdout and drain stderr CONCURRENTLY.

        Concurrently and not in sequence: the pipes are fixed-size, so a plugin that writes more to
        stderr than the buffer holds blocks forever while we sit reading stdout. That is a hang
        with no error, triggered by a chatty plugin, and it is the classic way to get one.

        A `model_request` is served on its OWN task for the same reason. Awaiting the model call
        inline would stop this loop, so the child's progress updates and its stderr would back up
        behind a network round-trip it is itself waiting on.
        """
        stderr_task = asyncio.create_task(self._drain(proc.stderr))
        writes = asyncio.Lock()  # the read loop and every broker task share one stdin
        served: set = set()
        try:
            proc.stdin.write(protocol.encode_job(job))
            await proc.stdin.drain()
            # stdin is NOT closed: it is how model responses get back to the child. It closes when
            # the process ends, which the child reads as "the host is gone" and unblocks any waiter.
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # the child died early; the result below will be None and reported as such
        final: dict | None = None
        while True:
            try:
                raw = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                log.warning("sandbox: dropping an over-long protocol line from '%s'", job["plugin_id"])
                continue
            except (OSError, asyncio.IncompleteReadError):
                break
            if not raw:
                break
            payload = protocol.decode_line(raw.decode("utf-8", "replace"))
            if payload is None:
                continue
            kind = payload.get("t")
            if kind == "update":
                if on_update is not None:
                    with contextlib.suppress(Exception):  # noqa: BLE001 — a UI callback must not kill the run
                        on_update(protocol.payload_result(payload))
            elif kind == "model_request":
                task = asyncio.create_task(
                    self._serve_model(proc, payload, broker, deadline, writes)
                )
                served.add(task)
                task.add_done_callback(served.discard)
            elif kind == "result":
                final = payload
        for task in list(served):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await proc.wait()
        return final, await stderr_task

    async def _serve_model(self, proc, request, broker, deadline, writes) -> None:
        """Answer one `model_request`. Never raises into the read loop."""
        deadline.pause()  # the child is blocked on US now; its own clock stops
        try:
            response = await broker.serve(request)
        except Exception as e:  # noqa: BLE001 — a broker bug must still unblock the child
            log.exception("sandbox: model broker failed")
            response = {
                "t": "model_response", "id": str(request.get("id") or ""),
                "ok": False, "text": "", "error": f"the host failed to serve the call: {e}",
            }
        finally:
            deadline.resume()
        async with writes:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
                proc.stdin.write(protocol.encode_job(response))
                await proc.stdin.drain()

    async def _watch_deadline(self, deadline, proc) -> None:
        """Kill the child once it has used its OWN time up. Polling rather than one `wait_for`,
        because the deadline pauses while the host is serving a model call for it."""
        while True:
            await asyncio.sleep(_DEADLINE_TICK_S)
            if deadline.expired():
                await self._terminate(proc)
                return

    @staticmethod
    async def _drain(stream) -> str:
        if stream is None:
            return ""
        try:
            return (await stream.read()).decode("utf-8", "replace")
        except (OSError, ValueError):
            return ""

    async def _kill_on_abort(self, abort, proc) -> None:
        if abort is None:
            return
        with contextlib.suppress(asyncio.CancelledError):
            await abort.wait()
            await self._terminate(proc)

    @staticmethod
    async def _terminate(proc) -> None:
        if proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        with contextlib.suppress(asyncio.CancelledError):
            await proc.wait()

    def _command(self) -> list[str]:
        """`-P -s -B`, and each letter is load-bearing:

        ``-P``  do NOT put the working directory on sys.path. The working directory IS the tenant
                workspace, so without this a file called ``agent_runtime.py`` sitting in a user's
                own files would be imported instead of our package — by the sandbox, at startup,
                before any guard is installed. (Python 3.11+.)
        ``-s``  no user site-packages: one more directory a plugin must not be able to preload from.
        ``-B``  no .pyc writing. The guard denies writes outside the grant, so bytecode caching
                would otherwise generate a denial on every single import — noise that would bury
                the denials that mean something.
        """
        python = self._setting("sandbox_python", "") or sys.executable
        return [str(python), "-P", "-s", "-B", "-m", "agent_runtime.infrastructure.tools.sandbox.worker"]

    def _child_env(self, grant: CapabilityGrant, workspace: str, temp_dir: str) -> dict:
        """An allowlist, never a copy-and-delete. A denylist over os.environ means every new
        provider key added anywhere leaks into every sandbox until someone remembers to add it."""
        names = self._setting("sandbox_env_passthrough", ()) or DEFAULT_ENV_PASSTHROUGH
        env = {n: os.environ[n] for n in names if os.environ.get(n)}
        # The child still has to import `agent_runtime` — the worker IS one of our modules. Handing
        # it the same root the parent imports from works whether this is a source checkout or an
        # installed distribution (in the latter it names site-packages, already importable).
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[4])
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        for var in ("TMPDIR", "TEMP", "TMP"):
            env[var] = temp_dir
        if workspace:
            # A library that writes to ~ lands in the workspace, which the grant already covers,
            # instead of the real home directory, which it does not.
            env["HOME"] = workspace
            env["USERPROFILE"] = workspace
        env.update({str(k): str(v) for k, v in (grant.secrets or {}).items()})
        return env

    def _posix_hardening(self, grant: CapabilityGrant) -> dict:
        """rlimits, and the uid drop when the operator configured one. POSIX only."""
        if os.name != "posix":
            return {}
        uid = int(self._setting("sandbox_child_uid", 0) or 0)
        gid = int(self._setting("sandbox_child_gid", 0) or 0)
        cpu_ms, mem_mb = int(grant.cpu_ms), int(grant.mem_mb)

        def preexec() -> None:  # pragma: no cover — runs between fork and exec
            from agent_runtime.infrastructure.tools.sandbox import child_guard

            child_guard.apply_rlimits(cpu_ms, mem_mb)
            if gid:
                os.setgid(gid)
            if uid:
                os.setuid(uid)

        if not (uid or gid or cpu_ms or mem_mb):
            return {}
        return {"preexec_fn": preexec}

    def _deny_paths(self) -> list[str]:
        """Paths the child must not read EVEN THOUGH they sit under an importable root.

        The child has to be able to import from wherever our package lives, and in a source
        checkout that is the repository — which also holds `v2/.env`. Rather than hope a rule
        written for module imports happens to exclude the secrets file, the daemon names what it
        knows is sensitive: its own config file, its state directory, and the tenant root holding
        every OTHER account's files. The run's own workspace is granted explicitly and wins, so
        the account still reaches its own data.
        """
        paths: list[str] = []
        for name in ("config_path", "state_dir", "tenant_root"):
            value = self._setting(name, "")
            if value:
                paths.append(str(value))
        try:
            from agent_runtime import runtime_paths

            paths.extend(str(p) for p in runtime_paths.env_files())
        except (ImportError, AttributeError):  # pragma: no cover — packaging variation
            pass
        vault = self._setting("credentials_path", "")
        if vault:
            paths.append(str(vault))
        return [p for p in paths if p]

    def _workspace(self, grant: CapabilityGrant, ctx: RunContext | None) -> str:
        for candidate in (*(grant.fs_paths or ()), getattr(ctx, "workspace", "") if ctx else ""):
            if candidate and Path(candidate).is_dir():
                return str(candidate)
        return ""

    def _setting(self, name: str, default):
        return getattr(self._config, name, default) if self._config is not None else default

    # ------------------------------------------------------------------ reporting

    def _record_denials(self, plugin_id: str, tool_name: str, denials: list) -> None:
        for denial in denials:
            capability = str((denial or {}).get("capability") or "unknown")
            telemetry.count(
                "sandbox_denied_total",
                source="sandbox",
                _props={"backend": self.name, "plugin_id": plugin_id, "tool": tool_name, "capability": capability},
            )
            log.warning(
                "sandbox: denied plugin '%s' tool '%s' -> %s (%s)",
                plugin_id, tool_name, capability, str((denial or {}).get("target") or "")[:200],
            )

    def _log_child_output(self, plugin_id: str, tool_name: str, text: str) -> None:
        cleaned = stdout_capture.scrub_text(text)
        if not cleaned:
            return
        telemetry.count(
            "plugin_stdout_total", source="plugin", _props={"plugin_id": plugin_id, "tool": tool_name}
        )
        logging.getLogger("agentd.plugin.stdout").info(
            "plugin output captured",
            extra={"plugin_id": plugin_id, "tool": tool_name, "source": "plugin", "plugin_output": cleaned},
        )

    def _fail(self, plugin_id: str, tool_name: str, outcome: str, message: str) -> ToolResult:
        telemetry.count(
            "sandbox_run_total",
            source="sandbox",
            _props={"backend": self.name, "plugin_id": plugin_id, "tool": tool_name, "outcome": outcome},
        )
        log.warning("sandbox: %s (%s/%s)", outcome, plugin_id, tool_name)
        return ToolResult.text(message, is_error=True)


def describe_job(job: dict) -> str:
    """A one-line, secret-free summary of a job — for debugging, and used by the tests to assert
    that nothing sensitive is in the payload."""
    return json.dumps(
        {k: v for k, v in job.items() if k in ("plugin_id", "tool_name", "entry", "grant")},
        sort_keys=True,
    )

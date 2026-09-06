"""MicrovmPluginSandbox — untrusted code leaves the box entirely: one Lambda microVM per call.

The subprocess backend isolates a tool in a child PROCESS — same kernel, same filesystem, same
network as the daemon. On a hosted daemon the rule is stricter: untrusted code never executes on
the machine that holds every tenant's data. This backend ships the call to the EXECUTOR service
(services/executor/handler.py), where the IDENTICAL sandbox worker runs inside a Firecracker
microVM built from the daemon's own image.

WHAT TRAVELS, AND HOW (pure HTTP — the daemon carries no AWS SDK, the executor presigns every
S3 URL this side touches, exactly the builder-service contract):

  code       the plugin's folder + its agent's shipped data (the grant's read_paths), zipped and
             CACHED by content fingerprint — plugin code changes rarely, so after the first call
             this upload disappears.
  workspace  the run's workspace (the grant's fs_paths), zipped up before and a CHANGED-FILES
             zip applied back after. Deletions never propagate — an untrusted tool must not be
             able to erase a workspace through the sync channel.
  brokering  the worker's model_request / fetch_request frames relay through S3 "slots": the
             executor writes req-N and polls res-N; THIS side polls presigned req-N GETs and
             answers through the SAME SandboxModelBroker / SandboxFetchBroker the subprocess
             backend uses. The grant check, the metering and the credential stay here — nothing
             in the microVM can make the call itself.

WHAT DOES NOT TRAVEL: grant.secrets (refused outright if a future resolver ever sets them — the
box must stay blind to values), the daemon's env, its config object (the same redacted
projection as the subprocess job), and every host path that was not explicitly synced.

`run_shell` at the bottom is the `exec` tool's hosted branch: same conveyor, no worker — one
command in the synced workspace, inside the microVM instead of on the daemon's box.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import tempfile
import time
import zipfile
from pathlib import Path

from agent_runtime.application.interfaces.tool import OnUpdate, Tool, ToolResult
from agent_runtime.application.run_context import RunContext
from agent_runtime.domain.sandbox import CapabilityGrant
from agent_runtime.infrastructure import telemetry
from agent_runtime.infrastructure.tools.sandbox import protocol
from agent_runtime.infrastructure.tools.sandbox.fetch_broker import SandboxFetchBroker
from agent_runtime.infrastructure.tools.sandbox.model_broker import SandboxModelBroker

log = logging.getLogger("agentd")

DEFAULT_TIMEOUT_S = 120.0

#: Transfer margin on top of the tool's own budget: zips up, cold start, zips down.
TRANSFER_MARGIN_S = 300.0

#: How often this side looks for the executor's next brokered request.
REQ_POLL_S = 0.35

#: Directories never shipped in a code zip. `ui`/`app` are the WINDOW (vite build inputs and
#: outputs — megabytes of modules no plugin reads); caches are noise.
_CODE_SKIP_DIRS = frozenset({"node_modules", "ui", "app", "__pycache__", ".git", ".vite"})

#: Directories never shipped in a workspace zip (recreatable noise; everything else is user data
#: and travels faithfully).
_WS_SKIP_DIRS = frozenset({"__pycache__", ".git"})


class MicrovmPluginSandbox:
    name = "microvm"

    def __init__(self, config=None) -> None:
        self._config = config
        self._url = str(getattr(config, "executor_url", "") or "").rstrip("/")
        self._key = str(getattr(config, "executor_internal_key", "") or "")
        # Same recipe store the subprocess backend keeps — SandboxedTool.register feeds it.
        self._plugins: dict[tuple[str, str], tuple[str, str]] = {}
        self._secrets: dict[tuple[str, str], tuple[str, ...]] = {}
        self._agent_dirs: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------------ wiring

    def register(self, plugin_id: str, tool_name: str, tool: Tool) -> None:
        entry = getattr(tool, "_plugin_entry", "") or ""
        root = getattr(tool, "_plugin_root", "") or ""
        self._plugins[(plugin_id, tool_name)] = (str(entry), str(root))
        self._secrets[(plugin_id, tool_name)] = tuple(
            str(s) for s in (getattr(tool, "_sandbox_secrets", ()) or ())
        )
        self._agent_dirs[(plugin_id, tool_name)] = str(
            getattr(tool, "_plugin_agent_dir", "") or ""
        )

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
        if not self._url:
            return self._fail(plugin_id, tool_name, "no-executor",
                              "microvm sandbox: AGENTD_EXECUTOR_URL is not configured — "
                              "untrusted tools cannot run on this daemon until it is.")
        entry, root = self._plugins.get((plugin_id, tool_name), ("", ""))
        if not entry:
            return self._fail(plugin_id, tool_name, "no-recipe",
                              f"microvm sandbox: no code recipe for '{tool_name}' "
                              f"(plugin '{plugin_id}') — it was registered without its entry/root.")
        if grant.secrets:
            # The resolver never sets these today; if one ever does, shipping values into a debug-
            # loggable job payload is exactly the blast radius the design refuses. Fail closed.
            return self._fail(plugin_id, tool_name, "secrets-refused",
                              "microvm sandbox: grant.secrets cannot travel to the executor — "
                              "declared secrets are broker-substituted, never handed over.")

        timeout = grant.timeout_s if grant.timeout_s > 0 else DEFAULT_TIMEOUT_S
        started = time.monotonic()
        workspace = self._workspace(grant, ctx)

        job = {
            "plugin_id": plugin_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "entry": entry,
            "plugin_root": root,  # remapped executor-side via the sync table
            "params": params or {},
            "grant": protocol.grant_payload(
                CapabilityGrant(
                    net_allowlist=grant.net_allowlist,
                    cpu_ms=grant.cpu_ms, mem_mb=grant.mem_mb,
                    timeout_s=timeout, models=grant.models,
                )
            ),
            "ctx": protocol.ctx_payload(ctx, plugin_id),
            "config": protocol.config_projection(
                self._config, plugin_id,
                tuple(getattr(self._config, "sandbox_config_fields", ()) or ()),
            ) if self._config is not None else {},
            "allow_native": bool(getattr(self._config, "sandbox_allow_native", False)),
        }

        try:
            code_zip, sync_table, fingerprint = self._code_zip(
                root, grant.read_paths, self._agent_dirs.get((plugin_id, tool_name), "")
            )
            job["_sync"] = sync_table
            ws_zip = self._zip_dir(workspace, _WS_SKIP_DIRS) if workspace else b""
        except OversizeError as e:
            return self._fail(plugin_id, tool_name, "oversize", f"microvm sandbox: {e}")

        try:
            slots = await self._ask({
                "op": "presign",
                "code_fingerprint": fingerprint,
                "workspace": bool(ws_zip),
                "broker": True,
            })
            await self._upload(slots, code_zip, ws_zip)
        except ExecutorError as e:
            return self._fail(plugin_id, tool_name, "transport", f"microvm sandbox: {e}")

        broker = SandboxModelBroker(self._config, plugin_id=plugin_id, tool_name=tool_name,
                                    grant=grant)
        fetcher = SandboxFetchBroker(self._config, plugin_id=plugin_id, tool_name=tool_name,
                                     grant=grant,
                                     declared_secrets=self._secrets.get((plugin_id, tool_name), ()))
        serve_task = asyncio.create_task(self._serve_slots(slots, broker, fetcher))
        try:
            run_params = {
                "op": "run",
                "job_id": slots.get("job_id"),
                "job": job,
                "timeout_s": timeout,
                "code_key": slots.get("code_key"),
                "workspace_key": slots.get("workspace_key") if ws_zip else "",
            }
            answer = await self._ask(run_params, timeout_s=timeout + TRANSFER_MARGIN_S,
                                     abort=abort)
        except ExecutorError as e:
            return self._fail(plugin_id, tool_name, "transport", f"microvm sandbox: {e}")
        finally:
            serve_task.cancel()
            try:
                await serve_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — teardown must not mask the answer
                pass

        for upd in answer.get("updates") or []:
            if on_update is not None:
                try:
                    on_update(protocol.payload_result(upd))
                except Exception:  # noqa: BLE001 — a UI callback must not kill the run
                    pass
        if answer.get("stderr_tail", "").strip():
            self._log_child_output(plugin_id, tool_name, answer["stderr_tail"])

        changes_url = str(answer.get("changes_url") or "")
        if changes_url and workspace:
            try:
                await self._apply_changes(changes_url, Path(workspace))
            except Exception as e:  # noqa: BLE001 — a sync failure must be loud, not silent
                return self._fail(
                    plugin_id, tool_name, "sync-back",
                    f"microvm sandbox: the tool ran but its workspace changes could not be "
                    f"applied: {e}",
                )

        result_frame = answer.get("result")
        if result_frame is None:
            return self._fail(plugin_id, tool_name, "no-result",
                              f"microvm sandbox: {answer.get('error') or 'the executor returned no result'}")

        self._record_denials(plugin_id, tool_name, result_frame.get("denials") or [])
        elapsed_ms = int((time.monotonic() - started) * 1000)
        outcome = "error" if result_frame.get("isError") else "ok"
        telemetry.count("sandbox_run_total", source="sandbox",
                        _props={"backend": self.name, "plugin_id": plugin_id,
                                "tool": tool_name, "outcome": outcome})
        telemetry.timing("sandbox_run_ms", elapsed_ms, source="sandbox",
                         _props={"backend": self.name, "plugin_id": plugin_id, "tool": tool_name})
        return protocol.payload_result(result_frame)

    # ------------------------------------------------------------------ enumeration

    def enumerate_tools(self, entry: str, plugin_root: str, agent_dir: str,
                        plugin_id: str) -> list[dict]:
        """Import the plugin INSIDE a microVM and return its tool specs — registration without
        running untrusted module code in this process. SYNC (called from plugin discovery, which
        runs before the event loop): plain blocking HTTP.

        Raises ExecutorError on any failure — the caller decides whether that means "no tools"
        or "fail the reload"; silently returning [] would disguise a broken executor as a
        plugin that ships nothing."""
        import httpx

        if not self._url:
            raise ExecutorError("AGENTD_EXECUTOR_URL is not configured")
        code_zip, sync_table, fingerprint = self._code_zip(plugin_root, (), agent_dir)
        job = {"plugin_id": plugin_id, "entry": entry, "plugin_root": plugin_root,
               "_sync": sync_table, "grant": {}, "config": {}}

        def ask(payload: dict, timeout: float) -> dict:
            r = httpx.post(f"{self._url}/executor", json=payload,
                           headers=self._headers(), timeout=timeout)
            answer = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if not isinstance(answer, dict) or (r.status_code >= 400 and not answer):
                raise ExecutorError(f"executor answered HTTP {r.status_code}: {r.text[:200]}")
            return answer

        slots = ask({"op": "presign", "code_fingerprint": fingerprint, "workspace": False,
                     "broker": False}, 60.0)
        if not slots.get("ok"):
            raise ExecutorError(str(slots.get("error") or "presign refused"))
        if not slots.get("code_cached") and slots.get("code_put_url"):
            r = httpx.put(slots["code_put_url"], content=code_zip, timeout=120.0)
            r.raise_for_status()
        answer = ask({"op": "enumerate", "job_id": slots.get("job_id"), "job": job,
                      "code_key": slots.get("code_key"), "timeout_s": 60}, 120.0)
        if not answer.get("ok"):
            raise ExecutorError(str(answer.get("error") or "enumerate failed"))
        return list(answer.get("specs") or [])

    # ------------------------------------------------------------------ transport

    def _headers(self) -> dict:
        return {"X-Internal-Key": self._key} if self._key else {}

    async def _ask(self, payload: dict, timeout_s: float = 60.0, abort=None) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            request = client.post(f"{self._url}/executor", json=payload, headers=self._headers())
            if abort is not None:
                post = asyncio.create_task(request)
                stop = asyncio.create_task(abort.wait())
                done, _pending = await asyncio.wait({post, stop},
                                                    return_when=asyncio.FIRST_COMPLETED)
                if post not in done:
                    post.cancel()
                    stop.cancel()
                    raise ExecutorError("the run was cancelled")
                stop.cancel()
                try:
                    r = post.result()
                except httpx.HTTPError as e:
                    raise ExecutorError(f"the executor is unreachable at {self._url}: {e}") from e
            else:
                try:
                    r = await request
                except httpx.HTTPError as e:
                    raise ExecutorError(f"the executor is unreachable at {self._url}: {e}") from e
        try:
            answer = r.json()
        except ValueError:
            raise ExecutorError(
                f"the executor answered HTTP {r.status_code} with a non-JSON body: {r.text[:200]}"
            ) from None
        if not isinstance(answer, dict):
            raise ExecutorError(f"the executor answered HTTP {r.status_code}: {r.text[:200]}")
        if not answer.get("ok") and answer.get("error") and "result" not in answer:
            raise ExecutorError(str(answer["error"]))
        return answer

    async def _upload(self, slots: dict, code_zip: bytes, ws_zip: bytes) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=300.0) as client:
            if not slots.get("code_cached") and slots.get("code_put_url"):
                r = await client.put(slots["code_put_url"], content=code_zip)
                r.raise_for_status()
            if ws_zip and slots.get("workspace_put_url"):
                r = await client.put(slots["workspace_put_url"], content=ws_zip)
                r.raise_for_status()

    async def _serve_slots(self, slots: dict, broker, fetcher) -> None:
        """The daemon's half of the broker channel: poll req-N, serve, PUT res-N. Sequential —
        one outstanding brokered call at a time, matching the executor's own pump."""
        import httpx

        req_urls = list(slots.get("broker_req_urls") or [])
        res_urls = list(slots.get("broker_res_urls") or [])
        n = 0
        async with httpx.AsyncClient(timeout=60.0) as client:
            while n < len(req_urls):
                try:
                    r = await client.get(req_urls[n])
                except httpx.HTTPError:
                    await asyncio.sleep(REQ_POLL_S)
                    continue
                if r.status_code != 200:
                    await asyncio.sleep(REQ_POLL_S)
                    continue
                try:
                    frame = json.loads(r.text)
                except ValueError:
                    frame = {}
                answer = await self._serve_frame(frame, broker, fetcher)
                try:
                    put = await client.put(res_urls[n], content=json.dumps(answer).encode("utf-8"))
                    put.raise_for_status()
                except httpx.HTTPError:
                    log.warning("microvm: could not deliver broker answer %d", n)
                n += 1

    async def _serve_frame(self, frame: dict, broker, fetcher) -> dict:
        kind = frame.get("t")
        try:
            if kind == "model_request":
                return await broker.serve(frame)
            if kind == "fetch_request":
                return await fetcher.serve(frame)
        except Exception as e:  # noqa: BLE001 — a broker bug must still unblock the far side
            log.exception("microvm: broker failed")
            return {"t": "model_response" if kind == "model_request" else "fetch_response",
                    "id": str(frame.get("id") or ""), "ok": False, "status": 0,
                    "headers": {}, "text": "", "url": "",
                    "error": f"the host failed to serve the call: {e}"}
        return {"t": "model_response", "id": str(frame.get("id") or ""), "ok": False,
                "text": "", "error": f"unknown broker frame '{kind}'"}

    async def _apply_changes(self, changes_url: str, workspace: Path) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.get(changes_url)
            r.raise_for_status()
        data = r.content
        root = workspace.resolve()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.infolist():
                target = (workspace / m.filename).resolve()
                if not str(target).startswith(str(root) + os.sep) and target != root:
                    raise ExecutorError(f"changes zip member escapes the workspace: {m.filename}")
            z.extractall(workspace)

    # ------------------------------------------------------------------ zips

    def _code_zip(self, plugin_root: str, read_paths, agent_dir: str) -> tuple[bytes, list, str]:
        """One zip carrying the plugin's code and its agent's shipped data, plus the sync table
        that tells the executor which job fields those trees satisfy. Fingerprinted for caching."""
        entries: list[tuple[str, Path, dict]] = []  # (zip prefix, host path, sync entry)
        if plugin_root and Path(plugin_root).is_dir():
            entries.append(("plugin", Path(plugin_root),
                            {"field": "plugin_root", "in": "code", "rel": "plugin"}))
        agent_base = Path(agent_dir) if agent_dir else None
        for i, raw in enumerate(read_paths or ()):
            p = Path(raw)
            if not p.exists():
                continue
            if plugin_root and str(p.resolve()).startswith(str(Path(plugin_root).resolve())):
                continue  # already inside the plugin tree
            rel = f"agent/{i}-{p.name}"
            entries.append((rel, p, {"field": "read_path", "in": "code", "rel": rel}))
        if agent_base and agent_base.is_dir():
            entries.append((None, agent_base, {"field": "agent_dir", "in": "code", "rel": "plugin"}))

        h = hashlib.sha256()
        buf = io.BytesIO()
        total = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for prefix, path, _entry in entries:
                if prefix is None:
                    continue  # marker-only entries (agent_dir) ship nothing extra
                if path.is_file():
                    st = path.stat()
                    total += st.st_size
                    h.update(f"{prefix}|{st.st_size}|{st.st_mtime_ns}\n".encode())
                    z.write(path, prefix)
                    continue
                for f in sorted(path.rglob("*")):
                    if not f.is_file():
                        continue
                    rel = f.relative_to(path)
                    if any(part in _CODE_SKIP_DIRS for part in rel.parts):
                        continue
                    st = f.stat()
                    total += st.st_size
                    if total > self._max_zip_bytes():
                        raise OversizeError(
                            f"the plugin/agent code tree exceeds the "
                            f"{self._max_zip_bytes() // (1024 * 1024)} MB sync ceiling"
                        )
                    h.update(f"{prefix}/{rel.as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
                    z.write(f, f"{prefix}/{rel.as_posix()}")
        sync_table = [e for _p, _path, e in entries if e["field"] != "agent_dir"]
        return buf.getvalue(), sync_table, h.hexdigest()

    def _zip_dir(self, directory: str, skip: frozenset) -> bytes:
        buf = io.BytesIO()
        total = 0
        base = Path(directory)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(base.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(base)
                if any(part in skip for part in rel.parts):
                    continue
                total += f.stat().st_size
                if total > self._max_zip_bytes():
                    raise OversizeError(
                        f"the workspace at {directory} exceeds the "
                        f"{self._max_zip_bytes() // (1024 * 1024)} MB sync ceiling — the microvm "
                        "backend cannot carry it; prune the workspace or raise "
                        "sandbox_limits.max_sync_mb"
                    )
                z.write(f, rel.as_posix())
        return buf.getvalue()

    def _max_zip_bytes(self) -> int:
        limits = dict(getattr(self._config, "sandbox_limits", None) or {}) if self._config else {}
        return int(limits.get("max_sync_mb") or 256) * 1024 * 1024

    @staticmethod
    def _workspace(grant: CapabilityGrant, ctx: RunContext | None) -> str:
        for candidate in (*(grant.fs_paths or ()), getattr(ctx, "workspace", "") if ctx else ""):
            if candidate and Path(candidate).is_dir():
                return str(candidate)
        return ""

    # ------------------------------------------------------------------ reporting

    def _record_denials(self, plugin_id: str, tool_name: str, denials: list) -> None:
        for denial in denials:
            capability = str((denial or {}).get("capability") or "unknown")
            telemetry.count("sandbox_denied_total", source="sandbox",
                            _props={"backend": self.name, "plugin_id": plugin_id,
                                    "tool": tool_name, "capability": capability})
            log.warning("sandbox: denied plugin '%s' tool '%s' -> %s (%s)",
                        plugin_id, tool_name, capability,
                        str((denial or {}).get("target") or "")[:200])

    def _log_child_output(self, plugin_id: str, tool_name: str, text: str) -> None:
        from agent_runtime.infrastructure.tools.sandbox import stdout_capture

        cleaned = stdout_capture.scrub_text(text)
        if not cleaned:
            return
        telemetry.count("plugin_stdout_total", source="plugin",
                        _props={"plugin_id": plugin_id, "tool": tool_name})
        logging.getLogger("agentd.plugin.stdout").info(
            "plugin output captured",
            extra={"plugin_id": plugin_id, "tool": tool_name, "source": "plugin",
                   "plugin_output": cleaned})

    def _fail(self, plugin_id: str, tool_name: str, outcome: str, message: str) -> ToolResult:
        telemetry.count("sandbox_run_total", source="sandbox",
                        _props={"backend": self.name, "plugin_id": plugin_id,
                                "tool": tool_name, "outcome": outcome})
        log.warning("sandbox: %s (%s/%s)", outcome, plugin_id, tool_name)
        return ToolResult.text(message, is_error=True)


class ExecutorError(Exception):
    """The executor could not be reached or refused — a transport fact, never the tool's fault."""


class OversizeError(Exception):
    """A tree is too large for the sync conveyor — an honest refusal, never a truncation."""


# ---------------------------------------------------------------------- the exec branch


async def run_shell(config, command: str, cwd: str, timeout_s: float,
                    env: dict | None = None) -> tuple[bool, str, dict]:
    """One shell command in a microVM with the caller's workspace synced through — the hosted
    branch of the `exec` tool. Returns (ok, output, meta); raises ExecutorError when the
    executor itself is unreachable (so the caller can say 'environment', not 'your command')."""
    backend = MicrovmPluginSandbox(config)
    if not backend._url:
        raise ExecutorError("AGENTD_EXECUTOR_URL is not configured")
    ws_zip = backend._zip_dir(cwd, _WS_SKIP_DIRS) if cwd and Path(cwd).is_dir() else b""
    slots = await backend._ask({"op": "presign", "workspace": bool(ws_zip), "broker": False})
    await backend._upload(slots, b"", ws_zip)
    answer = await backend._ask({
        "op": "shell",
        "job_id": slots.get("job_id"),
        "command": command,
        "timeout_s": timeout_s,
        "env": dict(env or {}),
        "workspace_key": slots.get("workspace_key") if ws_zip else "",
    }, timeout_s=timeout_s + TRANSFER_MARGIN_S)
    changes_url = str(answer.get("changes_url") or "")
    if changes_url and cwd:
        await backend._apply_changes(changes_url, Path(cwd))
    return bool(answer.get("ok")), str(answer.get("output") or ""), {
        "exit_code": answer.get("exit_code"),
    }

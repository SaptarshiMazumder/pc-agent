"""The CHILD: runs exactly one untrusted plugin tool call and exits. `python -m …sandbox.worker`.

    stdin  <- one JSON job LINE, then {"t":"model_response"} frames for as long as the call runs
    fd 1   -> JSON lines: {"t":"update"} / {"t":"model_request"}, then exactly one {"t":"result"}
    stderr -> whatever the plugin printed; the parent scrubs, caps and logs it

The channel is bidirectional because a sandboxed tool has no network and no credentials, so a model
call has to be asked of the host rather than made here (see child_models.py). stdin is therefore
read a LINE at a time and left open, not drained to EOF.

WHY FD 1 IS CLAIMED IMMEDIATELY. A plugin's `print` goes to stdout, and stdout is where the
protocol lives. One stray print would land in the middle of our JSON and the parent would read a
corrupt frame — a plugin could break the protocol by accident, or shape a print to forge a result
on purpose. So the first thing this module does is duplicate fd 1 for its own use and point the
plugin's stdout at stderr. After that the plugin cannot reach the protocol stream at all.

WHY THE LOADER IS NOT REUSED. `load_plugin_entry` swallows import/registration failures and returns
an empty list, which is right in the daemon — one broken plugin must not take down the others. In a
one-shot child there is nothing to degrade to, and "no tools" reads as "wrong tool name" when the
truth is "your plugin failed to import". So the load happens here, explicitly, and the exception is
the answer.

ORDER MATTERS. Everything this module needs is imported BEFORE the guard goes on; from that point
every open, socket and spawn — including the plugin's own imports — is policed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import types


def _claim_protocol_stream():
    """Take fd 1 for the protocol; send anything the plugin prints to stderr instead."""
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(protocol_fd, "w", encoding="utf-8", buffering=1)


def _emit(stream, payload: dict) -> None:
    stream.write(json.dumps(payload) + "\n")
    stream.flush()


def _load_tool(entry: str, plugin_root: str, tool_name: str, config, plugin_id: str):
    """Import the plugin, register it, and return the named tool. Raises with a real message."""
    import importlib

    from agent_runtime.application.interfaces.plugins import PluginContext
    from agent_runtime.infrastructure.plugins.api import CollectingPluginApi

    if plugin_root and plugin_root not in sys.path:
        sys.path.insert(0, plugin_root)
    module_name, _, func_name = (entry or "").partition(":")
    func_name = func_name or "register"
    module = importlib.import_module(module_name)
    register = getattr(module, func_name, None)
    if not callable(register):
        raise RuntimeError(f"plugin '{plugin_id}': {module_name} has no callable {func_name}")
    api = CollectingPluginApi()
    # EVERY runtime handle is None. That is the boundary, not an omission: PluginContext's handles
    # are all optional by contract, so a plugin that needs the browser or the credential vault
    # guards on None and degrades — it does not get a live one across this line.
    register(api, PluginContext(config=config, plugin_dir=plugin_root))
    for tool in api.tools:
        if getattr(tool, "name", "") == tool_name:
            return tool
    have = ", ".join(getattr(t, "name", "?") for t in api.tools) or "(none)"
    raise RuntimeError(f"plugin '{plugin_id}' registered no tool named '{tool_name}' (has: {have})")


def main() -> int:
    protocol = _claim_protocol_stream()
    # ONE LINE, not read-to-EOF: stdin stays open afterwards so the host can answer model requests.
    try:
        job = json.loads(sys.stdin.readline() or "{}")
    except ValueError as e:
        _emit(protocol, _error(f"sandbox worker: unreadable job ({e})"))
        return 2

    plugin_id = str(job.get("plugin_id") or "")
    tool_name = str(job.get("tool_name") or "")
    grant = job.get("grant") or {}

    # Imported before the guard: our own modules, and the ones the run needs.
    from agent_runtime.application.run_context import RunContext, set_run_context, set_trace_ids
    from agent_runtime.infrastructure.tools.sandbox import child_guard, child_models, child_net
    from agent_runtime.infrastructure.tools.sandbox import protocol as wire

    raw_ctx = job.get("ctx") or {}
    if raw_ctx:
        set_run_context(
            RunContext(
                agent_id=str(raw_ctx.get("agent_id") or ""),
                session_key=str(raw_ctx.get("session_key") or ""),
                mode=str(raw_ctx.get("mode") or ""),
                workspace=str(raw_ctx.get("workspace") or ""),
                run_id=str(raw_ctx.get("run_id") or ""),
                turn_id=str(raw_ctx.get("turn_id") or ""),
                # The agent's own per-tool overrides. resolve_tool_model reads these through
                # current_plugins(), which is a contextvar — so without restoring it here the child
                # resolves as if agent.toml had no [plugins.*] block at all.
                plugins=raw_ctx.get("plugins") or None,
                # The tenant fence, restored so check_read/check_write answer identically on
                # this side of the process boundary (the grant is still enforced separately).
                read_roots=tuple(raw_ctx.get("read_roots") or ()),
                write_clamp=tuple(raw_ctx.get("write_clamp") or ()),
            )
        )
        set_trace_ids(str(raw_ctx.get("run_id") or ""), str(raw_ctx.get("turn_id") or ""))

    # A namespace, NOT a dict-with-__getattr__: an unknown field must raise AttributeError so that
    # `getattr(config, "openai_api_key", "")` returns the caller's own default. A __getattr__ that
    # returned None would hand every such call a None the plugin then treats as a value.
    config = types.SimpleNamespace(**(job.get("config") or {}))

    # The event loop is built BEFORE the guard, and that ordering is not cosmetic: on Windows the
    # Proactor loop wakes itself through a loopback socketpair, so constructing it after the guard
    # means our own runtime trips the no-network rule and every single tool call fails as a denied
    # `socket.bind`. Set-up the child needs happens first; the guard covers everything after.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # The model bridge goes in BEFORE the plugin is loaded, for the same reason the guard does: a
    # plugin that did `from ...oneshot import text_complete` at module scope would otherwise bind
    # the real function, import litellm into this process, and try to dial out — which the guard
    # would then deny, correctly but uselessly.
    protocol_lock = threading.Lock()

    def send(frame: dict) -> None:
        with protocol_lock:  # the tool thread and the event loop can both write
            _emit(protocol, frame)

    bridge = child_models.ModelBridge(send)
    child_models.install(bridge)
    # The fetch bridge, on the same reasoning and the same channel: a plugin that imported
    # `outbound.fetch` at module scope would otherwise bind the real one, open a socket, and be
    # denied by the guard — correctly and uselessly, since the host will happily make the call.
    net_bridge = child_net.NetBridge(send)
    child_net.install(net_bridge)
    # `sys.stdin`, the SAME buffered object the job line was read from — a second wrapper over the
    # same fd would not see bytes already sitting in this one's buffer. ONE reader dispatches BOTH
    # brokers by frame type; two threads on one pipe would interleave partial lines.
    child_models.start_reader(
        sys.stdin, bridge, on_unknown=net_bridge.deliver, on_close=net_bridge.fail_all
    )

    denials = child_guard.install(
        fs_paths=grant.get("fs_paths") or (),
        read_paths=grant.get("read_paths") or (),
        net_allowlist=grant.get("net_allowlist") or (),
        plugin_root=str(job.get("plugin_root") or ""),
        temp_dir=str(job.get("temp_dir") or ""),
        deny_paths=job.get("deny_paths") or (),
        allow_native=bool(job.get("allow_native")),
    )

    try:
        tool = _load_tool(
            str(job.get("entry") or ""),
            str(job.get("plugin_root") or ""),
            tool_name,
            config,
            plugin_id,
        )
    except Exception as e:  # noqa: BLE001 — every load failure is the child's answer
        send(_error(f"sandbox: {type(e).__name__}: {e}", denials))
        return 1

    def on_update(partial) -> None:
        try:
            send(wire.result_payload(partial, kind="update"))
        except (OSError, ValueError):
            pass  # a broken pipe on progress must not abort the actual call

    async def run():
        return await tool.execute(
            str(job.get("tool_call_id") or ""), job.get("params") or {}, asyncio.Event(), on_update
        )

    try:
        result = loop.run_until_complete(run())
    except Exception as e:  # noqa: BLE001 — the plugin's exception is the tool's error result
        send(_error(f"{type(e).__name__}: {e}", denials))
        return 1
    finally:
        loop.close()

    payload = wire.result_payload(result)
    if denials:
        payload["denials"] = denials
    send(payload)
    return 0


def _error(message: str, denials: list | None = None) -> dict:
    payload = {
        "t": "result",
        "content": [{"type": "text", "text": message}],
        "isError": True,
        "artifacts": [],
    }
    if denials:
        payload["denials"] = denials
    return payload


if __name__ == "__main__":
    raise SystemExit(main())

"""A browser or MCP server, confined to one tenant/chat's disposable task.

The hard lifetime and idle deadline destroy the task even if a caller vanishes.
Every RPC and file transfer uses exact-key capabilities. No platform role exists.
"""
from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import json
import os
import time
import urllib.error
from pathlib import Path


def translate(value, before: str, after: str):
    if isinstance(value, str):
        return value.replace(before, after) if before else value
    if isinstance(value, list):
        return [translate(item, before, after) for item in value]
    if isinstance(value, dict):
        return {key: translate(item, before, after) for key, item in value.items()}
    return value


async def serve(payload: dict, s3) -> None:
    import handler

    from agent_runtime.application.run_context import RunContext, set_run_context
    from agent_runtime.config import Config, McpServerConfig

    params = payload["params"]
    kind = params["session_kind"]
    root = f"executor/jobs/{params['job_id']}/"
    workspace = Path("/tmp/session/workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)
    config = Config(workspace=str(workspace), state_dir=str(workspace))
    config.plugins = {"browser": {"tools": {"browser": {
        "headless": True, "persistent": False,
        "proxy": {"server": os.environ["HTTP_PROXY"]},
        "launch_args": ["--disable-quic", "--proxy-bypass-list=<-loopback>"],
    }}}}
    session = None
    browser = None
    if kind == "mcp":
        from agent_runtime.infrastructure.tools.mcp.session import create_session
        cfg = McpServerConfig(**params["config"])
        # Transport processes inherit only the task's public runtime + caller's own settings.
        session = await asyncio.wait_for(create_session(cfg), timeout=60)
    else:
        from agent_runtime.infrastructure.tools.browser.factory import build_browser_provider
        from agent_runtime.runtime_paths import builtin_plugins_dir
        browser = build_browser_provider(config)
        spec = importlib.util.spec_from_file_location("isolated_browser", builtin_plugins_dir() / "browser" / "browser_tool.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tool = module.BrowserTool(config, browser)
    expires = time.monotonic() + 1500
    idle = time.monotonic() + 300
    try:
        for index in range(256):
            request_key = root + f"rpc-in-{index}.json"
            while True:
                if time.monotonic() >= min(expires, idle):
                    return
                try:
                    request = json.loads(await asyncio.to_thread(
                        lambda key=request_key: s3.get_object(Bucket=payload["bucket"], Key=key)["Body"].read()
                    ))
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code not in (403, 404):
                        raise
                await asyncio.sleep(0.25)
            idle = time.monotonic() + 300
            if request.get("method") == "close":
                return
            try:
                async with asyncio.timeout(min(180, expires - time.monotonic())):
                    if request.get("workspace"):
                        archive = Path("/tmp/incoming.zip")
                        await asyncio.to_thread(s3.download_file, payload["bucket"], root + "workspace.zip", str(archive))
                        handler._unzip_guarded(archive, workspace)
                    before = handler._manifest(workspace)
                    arguments = translate(request.get("arguments", {}), params.get("host_workspace", ""), str(workspace))
                    set_run_context(RunContext(
                        agent_id="isolated", session_key="isolated", mode="cloud", workspace=str(workspace),
                        agent_dir="", hosted=True,
                        read_roots=(str(workspace),), write_clamp=(str(workspace),),
                    ))
                    try:
                        if kind == "mcp":
                            result = await session.list_tools() if request["method"] == "list_tools" else await session.call_tool(request["method"], arguments)
                        else:
                            if request["method"] == "fetch":
                                import sys

                                from agent_runtime.runtime_paths import builtin_plugins_dir
                                sys.path.insert(0, str(builtin_plugins_dir() / "web"))
                                from fetch.providers.browser_fetch import BrowserRenderProvider

                                result = await BrowserRenderProvider(browser).fetch(arguments["url"], arguments["max_chars"])
                            else:
                                result = await tool.execute("rpc", arguments, asyncio.Event())
                        wire = [dataclasses.asdict(item) for item in result] if isinstance(result, list) else dataclasses.asdict(result)
                    finally:
                        set_run_context(None)
                    changes = await asyncio.to_thread(handler._upload_changes, params, workspace, before)
                    reply = {"ok": True, "result": translate(wire, str(workspace), params.get("host_workspace", ""))}
                    if changes:
                        reply["changes_url"] = s3.generate_presigned_url("get_object", Params={"Bucket": payload["bucket"], "Key": changes})
            except Exception as exc:
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            await asyncio.to_thread(s3.put_object, Bucket=payload["bucket"], Key=root + f"rpc-out-{index}.json", Body=json.dumps(reply).encode())
    finally:
        if session:
            await session.close()
        if browser:
            await browser.close()

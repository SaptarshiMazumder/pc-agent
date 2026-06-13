"""Abort test: start a long exec, then chat.abort after 3s; expect agent_end aborted
and the session to accept a new run afterwards."""

import asyncio
import json
import sys
import uuid

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import websockets

SESSION = f"e2e-abort-{uuid.uuid4().hex[:6]}"


async def req(ws, method, params):
    rid = uuid.uuid4().hex[:8]
    await ws.send(json.dumps({"type": "req", "id": rid, "method": method, "params": params}))
    return rid


async def main():
    async with websockets.connect("ws://127.0.0.1:8787", max_size=20 * 1024 * 1024) as ws:
        await req(ws, "chat.send", {
            "sessionKey": SESSION,
            "message": "Run this exact shell command and wait for it: python -c \"import time; time.sleep(120); print('done')\"",
            "idempotencyKey": uuid.uuid4().hex,
        })

        sent_abort = False

        async def aborter():
            await asyncio.sleep(8)
            await req(ws, "chat.abort", {"sessionKey": SESSION})
            print("[sent chat.abort]")

        abort_task = asyncio.create_task(aborter())
        async with asyncio.timeout(60):
            async for raw in ws:
                frame = json.loads(raw)
                if frame["type"] == "res":
                    print(f"[res ok={frame.get('ok')}] {frame.get('payload')}")
                elif frame["type"] == "event":
                    p = frame["payload"]
                    if p.get("sessionKey") != SESSION:
                        continue
                    ev = p["event"]
                    if ev["type"] == "tool_execution_start":
                        print(f"  TOOL> {ev.get('toolName')}")
                    elif ev["type"] == "agent_end":
                        print(f"agent_end stopReason={ev.get('stopReason')}")
                        break
        abort_task.cancel()

        # session must be free again
        await req(ws, "chat.send", {
            "sessionKey": SESSION,
            "message": "Just say the word OK and nothing else, do not use tools.",
            "idempotencyKey": uuid.uuid4().hex,
        })
        async with asyncio.timeout(60):
            async for raw in ws:
                frame = json.loads(raw)
                if frame["type"] == "res":
                    print(f"[res2 ok={frame.get('ok')}] {frame.get('payload')}")
                    if not frame.get("ok"):
                        return
                elif frame["type"] == "event":
                    p = frame["payload"]
                    if p.get("sessionKey") != SESSION:
                        continue
                    ev = p["event"]
                    if ev["type"] == "agent_end":
                        print(f"second run agent_end stopReason={ev.get('stopReason')}")
                        return


asyncio.run(main())

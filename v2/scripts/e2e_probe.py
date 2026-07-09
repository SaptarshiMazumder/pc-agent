"""Scripted end-to-end probe: sends one message via chat.send and prints
the streamed events (compact). Usage:
  python -X utf8 scripts/e2e_probe.py "<message>" [session_key]
"""

import asyncio
import json
import sys
import uuid

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import websockets


async def main():
    message = sys.argv[1]
    session_key = sys.argv[2] if len(sys.argv) > 2 else f"e2e-{uuid.uuid4().hex[:6]}"
    print(f"[session: {session_key}]")

    async with websockets.connect("ws://127.0.0.1:8787", max_size=20 * 1024 * 1024) as ws:
        req_id = uuid.uuid4().hex[:8]
        await ws.send(
            json.dumps(
                {
                    "type": "req",
                    "id": req_id,
                    "method": "chat.send",
                    "params": {
                        "sessionKey": session_key,
                        "message": message,
                        "idempotencyKey": uuid.uuid4().hex,
                    },
                }
            )
        )
        text_parts = []
        async with asyncio.timeout(180):
            async for raw in ws:
                frame = json.loads(raw)
                if frame["type"] == "res":
                    ok = frame.get("ok")
                    print(f"[res ok={ok}] {frame.get('payload')}")
                    if not ok:
                        return
                elif frame["type"] == "event":
                    p = frame["payload"]
                    if p.get("sessionKey") != session_key:
                        continue
                    ev = p["event"]
                    t = ev["type"]
                    if t == "message_update":
                        if ev.get("kind") == "text_delta":
                            text_parts.append(ev.get("delta", ""))
                    elif t == "tool_execution_start":
                        args = json.dumps(ev.get("args", {}))[:120]
                        print(f"  TOOL> {ev.get('toolName')} {args}")
                    elif t == "tool_execution_end":
                        result = ev.get("result") or {}
                        blocks = result.get("content") or []
                        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                        flag = "ERR" if ev.get("isError") else "ok"
                        print(f"  TOOL< [{flag}] {text[:150].replace(chr(10), ' | ')}")
                    elif t == "agent_end":
                        print(f"\n=== FINAL ({ev.get('stopReason')}) ===")
                        print("".join(text_parts))
                        return


asyncio.run(main())

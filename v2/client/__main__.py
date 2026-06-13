"""Terminal REPL client: python -m client [--session <id>] [--url ws://...]

Sends chat.send over WebSocket and renders streamed chat.event frames:
text deltas live, tool activity as dim status lines.
Commands: /abort  /new  /quit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import websockets

DIM = "\x1b[2m"
RED = "\x1b[31m"
CYAN = "\x1b[36m"
RESET = "\x1b[0m"


def summarize_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 60:
            s = s[:60] + "…"
        parts.append(f"{k}={s}")
    return " ".join(parts)


class TerminalClient:
    def __init__(self, url: str, session_key: str):
        self.url = url
        self.session_key = session_key
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self.run_done = asyncio.Event()
        self.run_done.set()
        self._streamed_any_text = False

    async def request(self, method: str, params: dict) -> dict:
        req_id = uuid.uuid4().hex[:8]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[req_id] = fut
        await self.ws.send(json.dumps({"type": "req", "id": req_id, "method": method, "params": params}))
        return await fut

    async def _reader(self) -> None:
        try:
            async for raw in self.ws:
                frame = json.loads(raw)
                if frame.get("type") == "res":
                    fut = self.pending.pop(frame.get("id"), None)
                    if fut and not fut.done():
                        if frame.get("ok"):
                            fut.set_result(frame.get("payload") or {})
                        else:
                            fut.set_exception(
                                RuntimeError((frame.get("payload") or {}).get("error", "request failed"))
                            )
                elif frame.get("type") == "event":
                    payload = frame.get("payload") or {}
                    if payload.get("sessionKey") == self.session_key:
                        self._render(payload.get("event") or {})
        except websockets.ConnectionClosed:
            print(f"\n{RED}connection closed{RESET}")
            self.run_done.set()

    def _render(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "message_update":
            kind = event.get("kind")
            if kind == "text_delta":
                print(event.get("delta", ""), end="", flush=True)
                self._streamed_any_text = True
            elif kind == "thinking_delta":
                pass  # hide reasoning by default
        elif etype == "tool_execution_start":
            if self._streamed_any_text:
                print()
                self._streamed_any_text = False
            name = event.get("toolName", "?")
            print(f"{DIM}  ⚙ {name}: {summarize_args(event.get('args') or {})}{RESET}")
        elif etype == "tool_execution_end":
            if event.get("isError"):
                result = event.get("result") or {}
                blocks = result.get("content") or []
                text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                first_line = text.splitlines()[0] if text else "error"
                print(f"{RED}  ✗ {event.get('toolName')}: {first_line[:120]}{RESET}")
        elif etype == "continuation":
            if self._streamed_any_text:
                print()
                self._streamed_any_text = False
            print(f"{DIM}  ↻ continue ({event.get('reason')} #{event.get('attempt')}){RESET}")
        elif etype == "agent_end":
            if self._streamed_any_text:
                print()
                self._streamed_any_text = False
            reason = event.get("stopReason")
            if reason != "stop":
                print(f"{DIM}[run ended: {reason}]{RESET}")
            self.run_done.set()

    async def run(self) -> None:
        async with websockets.connect(self.url, max_size=20 * 1024 * 1024) as ws:
            self.ws = ws
            reader = asyncio.create_task(self._reader())
            print(f"{CYAN}connected to {self.url} | session: {self.session_key}{RESET}")
            print(f"{DIM}commands: /abort /new /quit{RESET}")
            try:
                while True:
                    try:
                        line = await asyncio.to_thread(input, "\n> ")
                    except (EOFError, KeyboardInterrupt):
                        break
                    line = line.strip()
                    if not line:
                        continue
                    if line == "/quit":
                        break
                    if line == "/new":
                        self.session_key = f"term-{uuid.uuid4().hex[:8]}"
                        print(f"{CYAN}new session: {self.session_key}{RESET}")
                        continue
                    if line == "/abort":
                        try:
                            payload = await self.request("chat.abort", {"sessionKey": self.session_key})
                            print(f"{DIM}{payload}{RESET}")
                        except RuntimeError as e:
                            print(f"{RED}{e}{RESET}")
                        continue

                    try:
                        self.run_done.clear()
                        await self.request(
                            "chat.send",
                            {
                                "sessionKey": self.session_key,
                                "message": line,
                                "idempotencyKey": uuid.uuid4().hex,
                            },
                        )
                        await self.run_done.wait()
                    except RuntimeError as e:
                        self.run_done.set()
                        print(f"{RED}{e}{RESET}")
            finally:
                reader.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description="agentd terminal client")
    parser.add_argument("--url", default="ws://127.0.0.1:8787")
    parser.add_argument("--session", default=None, help="session key to resume")
    args = parser.parse_args()
    session_key = args.session or f"term-{uuid.uuid4().hex[:8]}"
    client = TerminalClient(args.url, session_key)
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        pass
    print("bye")


if __name__ == "__main__":
    main()

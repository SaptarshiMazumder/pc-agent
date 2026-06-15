"""Terminal REPL client: python -m clients.terminal [--session <id>] [--url ws://...]

Sends chat.send over WebSocket and renders streamed chat.event frames using
`rich`: assistant text as live-rendered markdown, tool activity as Claude
Code-style blocks (⏺ call / ⎿ result), errors in red.
Commands: /sessions  /abort  /new  /quit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Make sure unicode glyphs (and rich's markdown bullets) survive legacy
# Windows code pages instead of raising UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import websockets
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# Signature accent for this client (distinct from Claude Code's orange).
LIME = "#a6e22e"

# A monochrome lime ramp: tools stay differentiable via shade, not hue.
LIME_BRIGHT = "#d7ff6e"
LIME_PALE = "#bef264"
LIME_MID = "#84cc16"
LIME_DEEP = "#65a30d"

# Override rich's default (blue) markdown link styling so URLs read lime.
# highlight=False stops rich's ReprHighlighter from recoloring URLs/paths/numbers
# (blue/cyan) inside the tool lines we style ourselves.
console = Console(
    highlight=False,
    theme=Theme(
        {
            "markdown.link": LIME,
            "markdown.link_url": f"underline {LIME_MID}",
        }
    ),
)

# Per-tool icon + lime shade, keyed by the gateway's real tool names, so each
# tool is distinguishable by both glyph and shade (all within the lime family).
TOOL_STYLE = {
    "read": ("◆", LIME_MID),
    "ls": ("▦", LIME_MID),
    "find": ("◎", LIME_MID),
    "write": ("◈", LIME_PALE),
    "edit": ("◈", LIME_PALE),
    "exec": ("▶", LIME),
    "process": ("▶", LIME),
    "web_search": ("◉", LIME_BRIGHT),
    "web_fetch": ("▼", LIME_BRIGHT),
    "browser": ("●", LIME_BRIGHT),
}
DEFAULT_TOOL_STYLE = ("⏺", LIME)


def tool_style(name: str) -> tuple[str, str]:
    return TOOL_STYLE.get(name.lower(), DEFAULT_TOOL_STYLE)


def summarize_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v).replace("\n", " ")
        if len(s) > 60:
            s = s[:60] + "…"
        parts.append(f"{k}={s}")
    return " ".join(parts)


def resolve_session_choice(sessions: list[dict], pick: str) -> str | None:
    """Map the user's `/sessions` pick (a 1-based index) to a sessionId.

    Returns None for blank/non-numeric/out-of-range input (i.e. cancel). Pure so
    it can be unit-tested without a live gateway or terminal.
    """
    pick = (pick or "").strip()
    if not pick.isdigit():
        return None
    idx = int(pick)
    if 1 <= idx <= len(sessions):
        return sessions[idx - 1].get("sessionId")
    return None


def sessions_table(sessions: list[dict], current: str | None = None) -> Table:
    """Numbered table of saved sessions (newest first, as the gateway returns)."""
    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("#", justify="right", style=f"bold {LIME}")
    table.add_column("session", style="bold")
    table.add_column("msgs", justify="right", style="dim")
    table.add_column("modified", style="dim")
    for i, s in enumerate(sessions, 1):
        sid = s.get("sessionId", "?")
        label = sid + ("  ← current" if sid == current else "")
        ts = s.get("modified")
        when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
        table.add_row(str(i), label, str(s.get("messages", 0)), when)
    return table


def result_text(result: dict) -> str:
    blocks = (result or {}).get("content") or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


class TerminalClient:
    def __init__(self, url: str, session_key: str):
        self.url = url
        self.session_key = session_key
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self.run_done = asyncio.Event()
        self.run_done.set()
        self._buf = ""
        self._mode: str | None = None  # "text" (final answer) or "think" (reasoning)
        self._live: Live | None = None

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
            self._close_live()
            console.print("\n[bold red]connection closed[/]")
            self.run_done.set()

    # --- live streaming of assistant text & reasoning --------------------

    def _renderable(self):
        """How the current buffer is drawn, depending on the stream mode.

        think -> muted italic block under a header, so reasoning is visually
        distinct from the final answer, which is rendered as bright markdown.
        """
        if self._mode == "think":
            return Group(
                Text("✻ thinking", style="grey50"),
                Padding(Text(self._buf, style="italic grey50"), (0, 0, 0, 2)),
            )
        return Markdown(self._buf)

    def _ensure_live(self, mode: str) -> None:
        # Switching between thinking and answer starts a fresh region so the
        # previous block stays on screen with its own styling.
        if self._live is not None and self._mode != mode:
            self._close_live()
        if self._live is None:
            self._mode = mode
            self._buf = ""
            self._live = Live(
                console=console,
                refresh_per_second=12,
                vertical_overflow="visible",
            )
            self._live.start()

    def _push(self, mode: str, delta: str) -> None:
        self._ensure_live(mode)
        self._buf += delta
        self._live.update(self._renderable())

    def _close_live(self) -> None:
        if self._live is not None:
            # leave the fully-rendered block on screen
            self._live.update(self._renderable() if self._buf else Text(""))
            self._live.stop()
            self._live = None
            self._mode = None
            self._buf = ""

    # --- event rendering -------------------------------------------------

    def _render(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "message_update":
            kind = event.get("kind")
            if kind == "text_delta":
                self._push("text", event.get("delta", ""))
            elif kind == "thinking_delta":
                self._push("think", event.get("delta", ""))
        elif etype == "tool_execution_start":
            self._close_live()
            name = event.get("toolName", "?")
            icon, color = tool_style(name)
            line = Text()
            # icon + full tool name sit inside a grey background block
            line.append(f" {icon} {name} ", style=f"bold {color} on grey23")
            line.append(f" ({summarize_args(event.get('args') or {})})", style="dim")
            console.print(line)
        elif etype == "tool_execution_end":
            name = event.get("toolName", "?")
            text = result_text(event.get("result") or {})
            first_line = (text.splitlines()[0] if text else "").strip()
            if event.get("isError"):
                console.print(Text(f"  ⎿ {first_line[:160] or 'error'}", style="red"))
            elif first_line:
                console.print(Text(f"  ⎿ {first_line[:160]}", style="dim"))
        elif etype == "continuation":
            self._close_live()
            console.print(
                Text(f"  ↻ continue ({event.get('reason')} #{event.get('attempt')})", style=f"dim {LIME_DEEP}")
            )
        elif etype == "agent_end":
            self._close_live()
            reason = event.get("stopReason")
            if reason and reason != "stop":
                console.print(Text(f"[run ended: {reason}]", style="dim"))
            self.run_done.set()

    async def run(self) -> None:
        async with websockets.connect(self.url, max_size=20 * 1024 * 1024) as ws:
            self.ws = ws
            reader = asyncio.create_task(self._reader())
            console.print(
                Panel.fit(
                    Group(
                        Text.from_markup(f"[bold black on {LIME}] agentd [/] terminal · [dim]{self.url}[/]"),
                        Text.from_markup(f"session [bold]{self.session_key}[/]"),
                        Text.from_markup("[dim]commands:[/] /sessions  /abort  /new  /quit"),
                    ),
                    border_style=LIME,
                    title="connected",
                    title_align="left",
                )
            )
            try:
                while True:
                    # Bracket the user's query in rules, like Claude / OpenClaw.
                    console.print(Rule(style=f"dim {LIME}"))
                    try:
                        line = await asyncio.to_thread(console.input, f"[bold black on {LIME}] › [/] ")
                    except (EOFError, KeyboardInterrupt):
                        break
                    console.print(Rule(style=f"dim {LIME}"))
                    line = line.strip()
                    if not line:
                        continue
                    if line == "/quit":
                        break
                    if line == "/new":
                        self.session_key = f"term-{uuid.uuid4().hex[:8]}"
                        console.print(f"[{LIME}]new session:[/] [bold]{self.session_key}[/]")
                        continue
                    if line == "/sessions":
                        try:
                            payload = await self.request("sessions.list", {})
                        except RuntimeError as e:
                            console.print(f"[red]{e}[/]")
                            continue
                        sessions = payload.get("sessions") or []
                        if not sessions:
                            console.print("[dim]no saved sessions yet[/]")
                            continue
                        console.print(sessions_table(sessions, self.session_key))
                        pick = await asyncio.to_thread(
                            console.input, "[dim]resume # (blank to cancel):[/] "
                        )
                        chosen = resolve_session_choice(sessions, pick)
                        if chosen is None:
                            console.print("[dim]cancelled[/]")
                        else:
                            self.session_key = chosen
                            console.print(
                                f"[{LIME}]resumed:[/] [bold]{self.session_key}[/] "
                                "[dim](history continues on your next message)[/]"
                            )
                        continue
                    if line == "/abort":
                        try:
                            payload = await self.request("chat.abort", {"sessionKey": self.session_key})
                            console.print(f"[dim]{payload}[/]")
                        except RuntimeError as e:
                            console.print(f"[red]{e}[/]")
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
                        self._close_live()
                        console.print(f"[red]{e}[/]")
            finally:
                self._close_live()
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
    console.print("[dim]bye[/]")


if __name__ == "__main__":
    main()

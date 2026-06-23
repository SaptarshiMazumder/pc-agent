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
from pygments.style import Style
from pygments.token import (
    Comment,
    Keyword,
    Name,
    Number,
    Operator,
    String,
    Token,
)
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# ── Color policy ──────────────────────────────────────────────────────────────
# Per user request: every *colored* element derives from lime green; greys,
# white and black are deliberately left untouched. The previous multi-hue values
# are kept inline as `# was: …` fallbacks so the old scheme can be restored.

# Signature accent for this client (distinct from Claude Code's orange).
LIME = "#a6e22e"

# A monochrome lime ramp: elements stay differentiable via shade, not hue.
LIME_BRIGHT = "#d7ff6e"
LIME_PALE = "#bef264"
LIME_MID = "#84cc16"
LIME_DEEP = "#65a30d"

# Semantic roles — formerly red/green/yellow. Green/yellow now map to lime
# shades (distinguished by brightness); ERROR stays RED on purpose (per user) so
# failures remain unmistakable. Old lime alternative kept inline as a fallback.
OK_COLOR = LIME             # was: "green"  -> lime (success / enabled)
RUNNING_COLOR = LIME_DEEP   # was: "yellow" -> lime (in-flight)
ERROR_COLOR = "red"         # KEPT red (per user request)   # lime fallback: LIME_BRIGHT


class _LimeCodeStyle(Style):
    """Monochrome-lime Pygments style for fenced code blocks, so syntax
    highlighting stays in the lime family instead of rich's default rainbow
    (monokai). Plain code text falls back to a neutral grey (left untouched)."""

    background_color = "#0c0c0c"
    styles = {
        Token: "#d4d4d4",              # default code text — neutral grey (kept)
        Comment: f"italic {LIME_DEEP}",
        Keyword: f"bold {LIME}",
        Operator: LIME,
        Name.Function: LIME_PALE,
        Name.Class: f"bold {LIME_PALE}",
        Name.Builtin: LIME_MID,
        String: LIME_MID,
        Number: LIME_MID,
    }


CODE_THEME = _LimeCodeStyle   # passed to Markdown(code_theme=…); was: rich default "monokai"

# Override rich's default (blue/cyan/magenta) markdown + repr styling so all
# colored output reads lime. highlight=False stops rich's ReprHighlighter from
# recoloring URLs/paths/numbers (blue/cyan); the markdown.* keys de-rainbow
# headings, inline code, block quotes and list bullets; the error/ok/running
# style names are reused as [markup] throughout.
console = Console(
    highlight=False,
    theme=Theme(
        {
            "markdown.link": LIME,
            "markdown.link_url": f"underline {LIME_MID}",
            "markdown.h1": f"bold {LIME_BRIGHT}",      # was: rich default (reverse/white)
            "markdown.h2": f"bold {LIME}",
            "markdown.h3": f"bold {LIME_PALE}",
            "markdown.h4": f"bold {LIME_MID}",
            "markdown.h5": LIME_MID,
            "markdown.h6": LIME_DEEP,
            "markdown.code": LIME,                     # was: "bold cyan"
            "markdown.item.bullet": f"bold {LIME}",    # was: "bold yellow"
            "markdown.item.number": f"bold {LIME}",    # was: "bold yellow"
            "markdown.block_quote": LIME_DEEP,         # was: "magenta"
            "markdown.hr": f"dim {LIME}",
            # semantic role styles — used as [error] / [ok] / [running] markup
            "error": f"bold {ERROR_COLOR}",            # red (kept per user)
            "ok": OK_COLOR,                            # was: "green" -> lime
            "running": RUNNING_COLOR,                  # was: "yellow" -> lime
        }
    ),
)

# Per-tool icon + lime shade, keyed by the gateway's real tool names, so each
# tool is distinguishable by both glyph and shade (all within the lime family).
TOOL_STYLE = {
    "update_plan": ("☑", LIME_BRIGHT),
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

# /sessions shows the most-recent N by default; 'all' (or a number) expands it.
SESSIONS_DEFAULT = 15


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


# Render the structured `update_plan` plan as a Claude-Code-style checklist. The
# backend emits ONLY structured data (the plan array of {step, status}); each client
# is free to render it however it likes — this is the terminal client's take. Other
# clients read the same `args.plan` and draw their own widget; nothing is coupled to
# this display.
PLAN_MARKS = {
    "completed": ("☒", "dim strike"),
    "in_progress": ("☐", f"bold {LIME_BRIGHT}"),
    "pending": ("☐", "dim"),
}


def render_plan(plan: list) -> Text:
    t = Text()
    rows = [s for s in plan if isinstance(s, dict)]
    for i, step in enumerate(rows):
        mark, style = PLAN_MARKS.get(step.get("status", "pending"), ("☐", "dim"))
        desc = str(step.get("step", "")).strip()
        t.append("  ⎿ " if i == 0 else "     ")  # ⎿ on the first row, aligned after
        t.append(f"{mark} {desc}", style=style)
        if i < len(rows) - 1:
            t.append("\n")
    return t


class TerminalClient:
    def __init__(self, url: str, session_key: str, agent_id: str | None = None):
        self.url = url
        self.session_key = session_key
        self.agent_id = agent_id        # explicit agent selection (None = the default agent)
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
                    if frame.get("event") == "notification":          # session-less, global
                        self._render_notification(frame.get("payload") or {})
                        continue
                    payload = frame.get("payload") or {}
                    if payload.get("sessionKey") == self.session_key:
                        self._render(payload.get("event") or {})
        except websockets.ConnectionClosed:
            self._close_live()
            console.print("\n[error]connection closed[/]")
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
        return Markdown(self._buf, code_theme=CODE_THEME)  # was: default monokai (rainbow)

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

    def _render_notification(self, n: dict) -> None:
        """A live notification pushed by the gateway (e.g. a scheduled run got blocked).
        Session-less — shown wherever you are, in a lime-bordered box."""
        self._close_live()
        body = Text()
        body.append(f"🔔 {n.get('text', 'notification')}\n", style=f"bold {LIME}")
        if n.get("detail"):
            body.append(n["detail"], style="dim")
        console.print(Panel.fit(
            body, border_style=LIME,
            title=f"notification · {n.get('kind', 'info')}", title_align="left"))

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
            args = event.get("args") or {}
            icon, color = tool_style(name)
            # update_plan: draw the structured plan as a checklist (Claude-Code style)
            if name == "update_plan" and isinstance(args.get("plan"), list):
                console.print(Text(f" {icon} Update Plan ", style=f"bold {color} on grey23"))
                console.print(render_plan(args["plan"]))
                return
            line = Text()
            # icon + full tool name sit inside a grey background block
            line.append(f" {icon} {name} ", style=f"bold {color} on grey23")
            line.append(f" ({summarize_args(args)})", style="dim")
            console.print(line)
        elif etype == "tool_execution_end":
            name = event.get("toolName", "?")
            text = result_text(event.get("result") or {})
            first_line = (text.splitlines()[0] if text else "").strip()
            if event.get("isError"):
                console.print(Text(f"  ⎿ {first_line[:160] or 'error'}", style="error"))
            elif first_line:
                console.print(Text(f"  ⎿ {first_line[:160]}", style="dim"))
        elif etype == "continuation":
            self._close_live()
            console.print(
                Text(f"  ↻ continue ({event.get('reason')} #{event.get('attempt')})", style=f"dim {LIME_DEEP}")
            )
        elif etype == "subagent_event":
            # nested-run visibility: a sub-agent's compact beats, relayed to the parent view
            self._close_live()
            child = event.get("childAgent", "?")
            kind = event.get("kind")
            if kind == "start":
                console.print(Text(f"  ▶ subagent {child} started", style=f"dim {LIME_DEEP}"))
            elif kind == "tool":
                console.print(Text(f"    ↳ {child} · {event.get('tool', '?')}", style="dim"))
            elif kind == "error":
                console.print(Text(f"  ✗ subagent {child}: {(event.get('detail') or 'error')[:120]}",
                                   style="error"))
            else:  # done
                console.print(Text(f"  ✓ subagent {child} done", style=f"dim {LIME}"))
        elif etype == "agent_end":
            self._close_live()
            reason = event.get("stopReason")
            if reason == "error":
                console.print(Text("[run ended: error]", style="error"))
                err = event.get("error")
                if err:  # surface the exact reason (rate limit, auth, etc.)
                    console.print(Text(str(err), style="error"))
            elif reason and reason != "stop":
                console.print(Text(f"[run ended: {reason}]", style="dim"))
            self.run_done.set()

    def _print_welcome(self, info: dict) -> None:
        """Welcome banner shown on connect, before the first prompt. Every fact
        (the agent's NAME included) comes from the gateway's `hello` handshake —
        the client hardcodes none of it."""
        name = info.get("agentName") or "the agent"
        model = info.get("model", "?")
        reasoning = info.get("reasoning", "off")
        url = info.get("gatewayUrl") or self.url
        agent_id = info.get("agentId", "main")
        sessions = info.get("sessions")
        saved = f" {sessions} saved session(s)." if sessions is not None else ""

        lines = [
            Text.from_markup(f"[bold {LIME}]Hi, I'm {name}.[/]"),
            Text(""),
            Text.from_markup("- Your personal agent — I act on [bold]this machine[/]: files, shell, web, a real browser."),
            Text.from_markup(f"- Using: [bold {LIME}]{model}[/] (thinking={reasoning})."),
            Text.from_markup(f"- Config: [bold {LIME}]valid[/]. Default agent: [bold]{agent_id}[/].{saved}"),
            Text.from_markup(f"- Gateway: reachable at [bold {LIME}]{url}[/]."),
            Text(""),
            Text.from_markup(
                f"Resume a past chat with [bold {LIME}]/sessions[/], or just start typing for a new one."
            ),
            Text.from_markup(f"[dim]agent[/] [bold]{self.agent_id or 'main'}[/]  [dim]session[/] [bold]{self.session_key}[/]   [dim]·  /agents  /agent <id>  /agent-rm <id>  /cron  /notifications  /sessions  /new  /quit[/]"),
        ]
        console.print(
            Panel.fit(Group(*lines), border_style=LIME, title=f"agentd · {name}", title_align="left")
        )

    async def run(self) -> None:
        async with websockets.connect(self.url, max_size=20 * 1024 * 1024) as ws:
            self.ws = ws
            reader = asyncio.create_task(self._reader())
            try:
                info = await self.request("hello", {})
            except RuntimeError:
                info = {}
            self._print_welcome(info)
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
                    if line == "/agents":
                        try:
                            payload = await self.request("agents.list", {})
                        except RuntimeError as e:
                            console.print(f"[error]{e}[/]")
                            continue
                        agents = payload.get("agents") or []
                        current = self.agent_id or payload.get("default") or "main"
                        for a in agents:
                            mark = f"[{LIME}]→[/]" if a["id"] == current else " "
                            console.print(f"  {mark} [bold]{a['id']}[/]  [dim]{a.get('name', '')}[/]")
                        console.print("[dim]switch with /agent <id> (use 'main' for the default)[/]")
                        continue
                    if line.startswith("/agent-rm") or line.startswith("/agent-delete"):
                        parts = line.split(maxsplit=1)
                        target = parts[1].strip() if len(parts) > 1 else ""
                        if not target:
                            console.print("[dim]usage: /agent-rm <id>  — permanently deletes the agent[/]")
                            continue
                        if target in ("main", "default"):
                            console.print("[error]cannot delete the default agent 'main'[/]")
                            continue
                        console.print(
                            f"[error]PERMANENTLY deletes[/] [bold]{target}[/]: "
                            "definition + workspace files + sessions + memory + cron jobs.")
                        confirm = await asyncio.to_thread(
                            console.input, f"[dim]type [bold]{target}[/] to confirm:[/] ")
                        if confirm.strip() != target:
                            console.print("[dim]cancelled[/]")
                            continue
                        try:
                            resp = await self.request("agents.remove", {"agentId": target})
                        except RuntimeError as e:
                            console.print(f"[error]{e}[/]")
                            continue
                        if not resp.get("removed"):
                            console.print(f"[error]{resp.get('error', 'failed')}[/]")
                            continue
                        cron = resp.get("cron") or {}
                        cron_n = sum(cron.values()) if isinstance(cron, dict) else 0
                        console.print(
                            f"[{LIME}]deleted[/] [bold]{target}[/]  "
                            f"[dim]sessions={resp.get('sessions')} · memory={resp.get('memory', 0)} rows"
                            f" · cron/ledger={cron_n} rows[/]")
                        if self.agent_id == target:        # we were on it -> hop back to main
                            self.agent_id = None
                            self.session_key = f"term-{uuid.uuid4().hex[:8]}"
                            console.print(f"[dim]switched to main (new session {self.session_key})[/]")
                        continue
                    if line == "/agent" or line.startswith("/agent "):
                        parts = line.split(maxsplit=1)
                        if len(parts) == 1:
                            console.print(f"[dim]current agent:[/] [bold]{self.agent_id or 'main'}[/]"
                                          "  [dim](/agents to list, /agent <id> to switch, /agent-rm <id> to delete)[/]")
                            continue
                        target = parts[1].strip()
                        self.agent_id = None if target in ("main", "default") else target
                        self.session_key = f"term-{uuid.uuid4().hex[:8]}"   # fresh thread per agent
                        console.print(f"[{LIME}]agent:[/] [bold]{self.agent_id or 'main'}[/] "
                                      f"[dim](new session {self.session_key})[/]")
                        continue
                    if line == "/cron" or line.startswith("/cron ") or line == "/jobs":
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] in ("history", "runs", "log"):
                            p = {"limit": 200}
                            if len(parts) >= 3:
                                p["id"] = parts[2]               # history for one job
                            try:
                                resp = await self.request("cron.runs", p)
                            except RuntimeError as e:
                                console.print(f"[error]{e}[/]")
                                continue
                            if not resp.get("autonomy"):
                                console.print("[dim]autonomy is off — set AGENTD_AUTONOMY=1[/]")
                                continue
                            hist = resp.get("runs") or []
                            if not hist:
                                console.print("[dim]no run history yet[/]")
                                continue
                            table = Table(show_header=True, header_style=f"bold {LIME}", box=None, pad_edge=False)
                            for col in ("started", "finished", "dur", "agent", "task", "status", "detail"):
                                table.add_column(col)
                            # error/failed = red (kept); ok = lime; blocked/aborted/running = lime-deep
                            colorof = {"ok": "ok", "error": "error", "failed": "error",
                                       "blocked": "running", "aborted": "running", "running": "running"}
                            for r in hist:
                                dur = f"{r['durationSec']}s" if r.get("durationSec") is not None else "—"
                                color = colorof.get(r["status"], "running")
                                table.add_row(r["startedAt"], r.get("finishedAt") or "—", dur,
                                              r["agentId"], r["taskId"], f"[{color}]{r['status']}[/]",
                                              (r.get("detail") or "")[:44])
                            console.print(table)
                            console.print(f"[dim]{len(hist)} run(s)[/]")
                            continue
                        if len(parts) >= 3 and parts[1] in ("rm", "remove", "run", "on", "off"):
                            sub, tid = parts[1], parts[2]
                            method = {"rm": "cron.remove", "remove": "cron.remove",
                                      "run": "cron.run", "on": "cron.update", "off": "cron.update"}[sub]
                            p = {"id": tid}
                            if sub == "on":
                                p["enabled"] = True
                            if sub == "off":
                                p["enabled"] = False
                            try:
                                resp = await self.request(method, p)
                                console.print(f"[{LIME}]{sub} {tid}[/] [dim]{resp}[/]")
                            except RuntimeError as e:
                                console.print(f"[error]{e}[/]")
                            continue
                        try:
                            payload = await self.request("cron.list", {})
                        except RuntimeError as e:
                            console.print(f"[error]{e}[/]")
                            continue
                        if not payload.get("autonomy"):
                            console.print("[dim]autonomy is off — set AGENTD_AUTONOMY=1 and restart the gateway[/]")
                            continue
                        jobs = payload.get("jobs") or []
                        if not jobs:
                            console.print("[dim]no scheduled jobs[/]")
                        else:
                            table = Table(show_header=True, header_style=f"bold {LIME}", box=None, pad_edge=False)
                            for col in ("id", "agent", "schedule", "next", "on", "payload"):
                                table.add_column(col)
                            for j in jobs:
                                table.add_row(
                                    j["id"], j["agentId"], j["schedule"], j["nextDue"],
                                    "[ok]on[/]" if j["enabled"] else "[dim]off[/]",
                                    (j["payload"] or "")[:48])
                            console.print(table)
                        runs = payload.get("runs") or []
                        if runs:
                            console.print("[dim]recent runs:[/]")
                            for r in runs[:5]:
                                tail = f" [dim]— {r['detail']}[/]" if r.get("detail") else ""
                                console.print(f"  [dim]{r['at']}[/] {r['agentId']} [{r['taskId']}] {r['status']}{tail}")
                        if jobs:
                            console.print("[dim]manage: /cron rm <id> · /cron run <id> · /cron off|on <id> · /cron history [id][/]")
                        continue
                    if line == "/cleanup" or line.startswith("/cleanup "):
                        # /cleanup [agent] [--pat=GLOB ...] [--yes]
                        # dry-run preview by default; --yes (or -y) actually deletes.
                        parts = line.split()
                        agent = self.agent_id or "main"
                        apply = False
                        pats: list[str] = []
                        for tok in parts[1:]:
                            if tok in ("--yes", "-y", "yes"):
                                apply = True
                            elif tok.startswith("--pat="):
                                pats.append(tok[len("--pat="):])
                            else:
                                agent = tok.strip().lower()        # positional = agent id
                        try:
                            resp = await self.request(
                                "workspace.cleanup", {"agentId": agent, "apply": apply, "patterns": pats})
                        except RuntimeError as e:
                            console.print(f"[error]{e}[/]")
                            continue
                        if resp.get("error"):
                            console.print(f"[error]{resp['error']}[/]")
                            continue
                        files = (resp.get("deleted") if apply else resp.get("wouldDelete")) or []
                        if not files:
                            console.print(f"[dim]{agent}: nothing to clean (tmp/ empty, no pattern matches)[/]")
                            continue
                        verb = "deleted" if apply else "would delete"
                        console.print(f"[{LIME}]{agent}: {verb} {len(files)} file(s)[/]")
                        for f in files[:40]:
                            console.print(f"  [dim]{f}[/]")
                        if len(files) > 40:
                            console.print(f"  [dim]… and {len(files) - 40} more[/]")
                        if not apply:
                            console.print(f"[dim]run `/cleanup {agent} --yes` to delete (add --pat=GLOB to target more)[/]")
                        continue
                    if line in ("/notifications", "/notifs", "/n") or line.startswith(("/notifications ", "/notifs ")):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] in ("ack", "read"):
                            nid = parts[2] if len(parts) >= 3 else "*"
                            try:
                                resp = await self.request("notifications.ack", {"id": nid})
                                console.print(f"[dim]acked {resp.get('acked', 0)}[/]")
                            except RuntimeError as e:
                                console.print(f"[error]{e}[/]")
                            continue
                        try:
                            payload = await self.request("notifications.list", {})
                        except RuntimeError as e:
                            console.print(f"[error]{e}[/]")
                            continue
                        if not payload.get("autonomy"):
                            console.print("[dim]autonomy is off — no notifications[/]")
                            continue
                        ns = payload.get("notifications") or []
                        if not ns:
                            console.print("[dim]no notifications[/]")
                            continue
                        for x in ns:
                            mark = "[bold]●[/]" if not x["read"] else "[dim]○[/]"
                            kc = "error" if x["kind"] == "failed" else "ok"
                            tail = f" [dim]— {x['detail']}[/]" if x.get("detail") else ""
                            console.print(f"  {mark} [dim]{x['at']}[/] [{kc}]{x['kind']}[/] "
                                          f"[bold]{x['agentId']}[/] {x['text']}{tail}  [dim]{x['id']}[/]")
                        console.print("[dim]ack: /notifications ack <id>  (or 'ack' for all)[/]")
                        continue
                    if line == "/sessions" or line.startswith("/sessions "):
                        # scope to the agent we're on — each agent has its own threads
                        parts = line.split(maxsplit=1)
                        arg = parts[1].strip().lower() if len(parts) > 1 else ""
                        params = {"agentId": self.agent_id} if self.agent_id else {}
                        try:
                            payload = await self.request("sessions.list", params)
                        except RuntimeError as e:
                            console.print(f"[error]{e}[/]")
                            continue
                        sessions = payload.get("sessions") or []   # newest first (gateway-sorted)
                        who = payload.get("agentId") or self.agent_id or "main"
                        if not sessions:
                            console.print(f"[dim]no saved sessions for agent '{who}' yet[/]")
                            continue
                        # how many to show: default 15; 'all'/'*' or a number expands it
                        if arg in ("all", "*", "more"):
                            n = len(sessions)
                        elif arg.isdigit():
                            n = max(1, int(arg))
                        else:
                            n = SESSIONS_DEFAULT
                        while True:                                # re-render if they ask for 'all'
                            shown = sessions[:n]
                            console.print(f"[dim]sessions for agent[/] [bold]{who}[/] "
                                          f"[dim](showing {len(shown)} of {len(sessions)}):[/]")
                            console.print(sessions_table(shown, self.session_key))
                            hint = "resume # (blank to cancel"
                            if n < len(sessions):
                                hint += f", 'all' to see {len(sessions)}"
                            pick = await asyncio.to_thread(console.input, f"[dim]{hint}):[/] ")
                            if (pick or "").strip().lower() in ("all", "*", "more") and n < len(sessions):
                                n = len(sessions)
                                continue
                            break
                        chosen = resolve_session_choice(shown, pick)
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
                            console.print(f"[error]{e}[/]")
                        continue

                    try:
                        self.run_done.clear()
                        params = {
                            "sessionKey": self.session_key,
                            "message": line,
                            "idempotencyKey": uuid.uuid4().hex,
                        }
                        if self.agent_id:
                            params["agentId"] = self.agent_id   # backend resolves the agent
                        await self.request("chat.send", params)
                        await self.run_done.wait()
                    except RuntimeError as e:
                        self.run_done.set()
                        self._close_live()
                        console.print(f"[error]{e}[/]")
            finally:
                self._close_live()
                reader.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description="agentd terminal client")
    parser.add_argument("--url", default="ws://127.0.0.1:8787")
    parser.add_argument("--session", default=None, help="session key to resume")
    parser.add_argument("--agent", default=None,
                        help="agent id to talk to (default: the gateway's default agent)")
    args = parser.parse_args()
    session_key = args.session or f"term-{uuid.uuid4().hex[:8]}"
    client = TerminalClient(args.url, session_key, agent_id=args.agent)
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        pass
    console.print("[dim]bye[/]")


if __name__ == "__main__":
    main()

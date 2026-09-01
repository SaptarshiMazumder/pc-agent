"""Terminal REPL client: python -m clients.terminal [--session <id>] [--url ws://...]

Sends chat.send over WebSocket and renders streamed chat.event frames using
`rich`: assistant text as live-rendered markdown, tool activity as Claude
Code-style blocks (⏺ call / ⎿ result), errors in red.
Press `/` for the full command palette (agents, sessions, projects, tools,
cron, mcp, notifications, cleanup, delete, new, quit).

Anything that used to require typing an index or id (/sessions, /agents,
/agent-rm, /mcp remove, /cron rm|run|on|off, /notifications ack) now opens an
arrow-key menu (↑↓ + enter, esc cancels — see picker.py) when running in a
real terminal; the typed flows remain as the non-TTY fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

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

from agent_runtime.clients.timefmt import whatsapp_when

from . import picker

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

# The "/" command palette (press "/" on an empty prompt). Every command here
# must run sensibly with no arguments — most open an arrow-key menu of their
# own; esc in the palette prefills "/" so arguments can still be typed.
COMMANDS = [
    ("/sessions", "list & resume this agent's saved sessions"),
    ("/recents", "recent chats across ALL agents"),
    ("/session", "current chat: rename / move / duplicate / delete"),
    ("/delete", "delete a saved session — permanent"),
    ("/projects", "projects: list / pick / new / rename / lead / members / chats"),
    ("/agents", "list agents & switch"),
    ("/agent", "show / switch the current agent"),
    ("/agent-info", "agent detail: identity, skills, workspace files"),
    ("/agent-rm", "delete an agent — permanent"),
    ("/workspace", "browse & manage the workspace (agent or project)"),
    ("/tools", "tools available to the current agent"),
    ("/capabilities", "everything the runtime exposes: tools / plugins / skills / agents"),
    ("/models", "every model in use: brain + tools, and what each resolves to"),
    ("/store", "install / remove agents (marketplace)"),
    ("/config", "view or set configuration"),
    ("/mcp", "MCP servers: list / add / remove"),
    ("/cron", "scheduled jobs: list / run / on / off / history"),
    ("/cleanup", "clean an agent workspace (dry-run first)"),
    ("/notifications", "list & ack notifications"),
    ("/new", "start a fresh session"),
    ("/abort", "abort the current run"),
    ("/quit", "exit"),
]


def command_options() -> list[picker.Option]:
    return [picker.Option(value=cmd, label=cmd, detail=desc) for cmd, desc in COMMANDS]


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
    """Numbered table of saved sessions (newest first, as the gateway returns).
    Shows the server-side TITLE first (auto/LLM/user), the raw key dim, and a
    WhatsApp-style 'when' (14:32 / Yesterday / Tuesday / 5 June / 3 Apr 1996)."""
    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("#", justify="right", style=f"bold {LIME}")
    table.add_column("title", style="bold")
    table.add_column("session", style="dim")
    table.add_column("msgs", justify="right", style="dim")
    table.add_column("when", style="dim")
    for i, s in enumerate(sessions, 1):
        sid = s.get("sessionId", "?")
        title = s.get("title") or sid
        label = title + ("  ← current" if sid == current else "")
        table.add_row(str(i), label, sid, str(s.get("messages", 0)),
                      whatsapp_when(s.get("modified") or 0))
    return table


def session_options(sessions: list[dict], current: str | None = None) -> list[picker.Option]:
    """Sessions -> picker options: the same facts as sessions_table, menu-shaped.
    Pure so it can be unit-tested without a live gateway or terminal."""
    opts = []
    for s in sessions:
        sid = s.get("sessionId", "?")
        when = whatsapp_when(s.get("modified") or 0)
        detail = f"{sid} · {s.get('messages', 0)} msgs" + (f" · {when}" if when else "")
        opts.append(picker.Option(value=sid, label=s.get("title") or sid, detail=detail,
                                  current=sid == current))
    return opts


def result_text(result: dict) -> str:
    blocks = (result or {}).get("content") or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def coerce_scalar(raw: str):
    """A typed config value from a CLI string: bool / int / float, else the string.
    Pure so `/config set` writes the right JSON type (true, 5, 0.3, "text")."""
    low = raw.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


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
    def __init__(self, url: str, session_key: str, agent_id: str | None = None,
                 project_id: str | None = None):
        self.url = url
        self.session_key = session_key
        self.agent_id = agent_id        # explicit agent selection; resolved from hello on connect
        self._server_default = "main"   # the gateway's default agent (from hello.agentId)
        self.project_id = project_id    # active project — new chats land in it (None = standalone)
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self.run_done = asyncio.Event()
        self.run_done.set()
        self._buf = ""
        self._mode: str | None = None  # "text" (final answer) or "think" (reasoning)
        self._live: Live | None = None
        self.history: list[str] = []   # prompt history for ↑/↓ recall

    async def request(self, method: str, params: dict) -> dict:
        req_id = uuid.uuid4().hex[:8]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[req_id] = fut
        await self.ws.send(json.dumps({"type": "req", "id": req_id, "method": method, "params": params}))
        return await fut

    async def _pick(self, title: str, options: list[picker.Option]):
        """Arrow-key menu, off the event loop thread (it blocks on raw keys)."""
        return await asyncio.to_thread(picker.pick, console, title, options)

    def _switch_agent(self, target: str) -> None:
        # 'default' is a client alias for whatever agent the gateway defaults to (which may be
        # a flavored install's specialist, not 'main'); every other target is a real agent id.
        self.agent_id = self._server_default if target == "default" else target
        self.session_key = f"term-{uuid.uuid4().hex[:8]}"   # fresh thread per agent
        console.print(f"[{LIME}]agent:[/] [bold]{self.agent_id}[/] "
                      f"[dim](new session {self.session_key})[/]")

    def _resume_session(self, session_key: str, rows: list) -> None:
        """Resume a saved chat AND adopt its project. The send path re-stamps the active
        project onto the session on every turn, so without syncing project_id here, resuming a
        standalone (or other-project) chat while a project is active would silently move it."""
        self.session_key = session_key
        row = next((s for s in rows if s.get("sessionId") == session_key), None)
        self.project_id = (row.get("projectId") or None) if row else None
        where = f" [dim](project {self.project_id})[/]" if self.project_id else ""
        console.print(f"[{LIME}]resumed:[/] [bold]{session_key}[/]{where} "
                      "[dim](history continues on your next message)[/]")

    # ---- desktop-parity commands ---------------------------------------------
    # Everything the desktop can do, furnished for the REPL: cross-agent recents, current-chat
    # ops (rename/move/duplicate/delete), full project management, workspace browsing, agent
    # detail, the store, and config. Dispatched from _extra_command so the main loop stays lean.

    async def _extra_command(self, line: str) -> bool:
        """Handle a parity command; return True iff it consumed `line`."""
        parts = line.split()
        cmd, args = parts[0], parts[1:]
        if cmd == "/recents":
            await self._cmd_recents()
        elif cmd == "/session":
            await self._cmd_session(args)
        elif cmd in ("/workspace", "/ws"):
            await self._cmd_workspace(args)
        elif cmd == "/agent-info":
            await self._cmd_agent_info(args)
        elif cmd == "/store":
            await self._cmd_store(args)
        elif cmd in ("/capabilities", "/caps"):
            await self._cmd_capabilities(args)
        elif cmd == "/models":
            await self._cmd_models(args)
        elif cmd == "/config":
            await self._cmd_config(args)
        elif cmd == "/projects" and args and args[0] in ("rename", "lead", "member", "members", "chats"):
            await self._cmd_projects_extra(args[0], args[1:])
        else:
            return False
        return True

    async def _pick_agent(self, title: str):
        """Arrow-menu pick of an agent id (or None); prints the list + returns None off-TTY."""
        try:
            payload = await self.request("agents.list", {})
        except RuntimeError as e:
            console.print(f"[error]{e}[/]"); return None
        agents = payload.get("agents") or []
        if not agents:
            console.print("[dim]no agents[/]"); return None
        if not picker.can_pick(console):
            console.print("[dim]agents: " + ", ".join(a["id"] for a in agents) + "[/]"); return None
        return await self._pick(title, [
            picker.Option(value=a["id"], label=a["id"],
                          detail=" · ".join(x for x in (a.get("name", ""), a.get("tagline", "")) if x),
                          current=a["id"] == self.agent_id)
            for a in agents])

    async def _project_options(self, *, standalone: bool = False) -> list:
        payload = await self.request("projects.list", {})
        rows = payload.get("projects") or []
        opts = []
        if standalone:
            opts.append(picker.Option(value="", label="(standalone — no project)"))
        opts += [picker.Option(value=p["id"], label=p["name"], detail=p["id"],
                               current=p["id"] == self.project_id) for p in rows]
        return opts

    async def _pick_project(self, title: str):
        """Arrow-menu pick of a project id (or None)."""
        try:
            opts = await self._project_options()
        except RuntimeError as e:
            console.print(f"[error]{e}[/]"); return None
        if not opts:
            console.print("[dim]no projects — /projects new <name>[/]"); return None
        if not picker.can_pick(console):
            console.print("[dim]projects: " + ", ".join(str(o.value) for o in opts) + "[/]"); return None
        return await self._pick(title, opts)

    def _ws_params(self, extra: dict | None = None) -> dict:
        """Workspace RPC scope: the ACTIVE project's shared workspace wins, else this agent's."""
        p = dict(extra or {})
        if self.project_id:
            p["projectId"] = self.project_id
        else:
            p["agentId"] = self.agent_id
        return p

    async def _cmd_recents(self) -> None:
        """Recent chats across EVERY agent (desktop's cross-agent Recents). Resuming a row
        switches to that row's agent + project, so history and workspace binding stay correct."""
        try:
            payload = await self.request("sessions.list", {"all": True})
        except RuntimeError as e:
            console.print(f"[error]{e}[/]"); return
        rows = payload.get("sessions") or []
        if not rows:
            console.print("[dim]no chats yet[/]"); return
        if not picker.can_pick(console):
            for i, s in enumerate(rows[:SESSIONS_DEFAULT], 1):
                console.print(f"  [{LIME}]{i}[/] [bold]{s.get('title') or s['sessionId']}[/] "
                              f"[dim]{s.get('agentId', '?')} · {whatsapp_when(s.get('modified') or 0)}[/]")
            return
        opts = [picker.Option(
            value=s["sessionId"], label=s.get("title") or s["sessionId"],
            detail=f"{s.get('agentId', '?')}"
                   + (f" · {s['projectId']}" if s.get("projectId") else "")
                   + f" · {whatsapp_when(s.get('modified') or 0)}",
            current=s["sessionId"] == self.session_key) for s in rows]
        chosen = await self._pick(f"recents — all agents ({len(rows)})", opts)
        if chosen is None:
            console.print("[dim]cancelled[/]"); return
        row = next((s for s in rows if s["sessionId"] == chosen), {})
        self.agent_id = row.get("agentId") or self.agent_id
        self.project_id = row.get("projectId") or None
        self.session_key = chosen
        where = f" · project {self.project_id}" if self.project_id else ""
        console.print(f"[{LIME}]resumed:[/] [bold]{chosen}[/] [dim](agent {self.agent_id}{where})[/]")

    async def _cmd_session(self, args: list) -> None:
        """Ops on the CURRENT chat: rename / move to a project / duplicate / delete."""
        sub = args[0] if args else ""
        rest = " ".join(args[1:]).strip()
        key, aid = self.session_key, self.agent_id
        if sub == "rename":
            title = rest or (await asyncio.to_thread(
                console.input, "[dim]new title (blank clears the manual name):[/] ")).strip()
            resp = await self.request("sessions.rename",
                                      {"sessionKey": key, "agentId": aid, "title": title})
            console.print(f"[{LIME}]renamed[/] [dim]{resp.get('title') or '(auto title)'}[/]"
                          if resp.get("ok") else f"[error]{resp.get('error', 'failed')}[/]")
        elif sub in ("move", "project"):
            pid = rest
            if not pid and picker.can_pick(console):
                pid = await self._pick("move chat to a project", await self._project_options(standalone=True))
                if pid is None:
                    console.print("[dim]cancelled[/]"); return
            resp = await self.request("sessions.move",
                                      {"sessionKey": key, "agentId": aid, "projectId": pid})
            if resp.get("ok"):
                self.project_id = pid or None
                console.print(f"[{LIME}]moved[/] [dim]{('→ ' + pid) if pid else 'standalone'}[/]")
            else:
                console.print(f"[error]{resp.get('error', 'failed')}[/]")
        elif sub in ("duplicate", "dup", "copy"):
            resp = await self.request("sessions.duplicate", {"sessionKey": key, "agentId": aid})
            if resp.get("ok"):
                self.session_key = resp["sessionKey"]
                console.print(f"[{LIME}]duplicated[/] [bold]{self.session_key}[/] [dim](now active)[/]")
            else:
                console.print(f"[error]{resp.get('error', 'failed')}[/]")
        elif sub in ("delete", "rm"):
            confirm = (await asyncio.to_thread(
                console.input, f"[error]delete[/] this chat [dim]({key})? (y/N):[/] ")).strip().lower()
            if confirm not in ("y", "yes"):
                console.print("[dim]cancelled[/]"); return
            resp = await self.request("sessions.delete", {"sessionKey": key, "agentId": aid})
            if resp.get("ok"):
                self.session_key = f"term-{uuid.uuid4().hex[:8]}"
                console.print(f"[{LIME}]deleted[/] [dim]new session {self.session_key}[/]")
            else:
                console.print(f"[error]{resp.get('error', 'failed')}[/]")
        else:
            console.print("[dim]/session rename [title] · move [project] · duplicate · delete[/] "
                          f"[dim](current: {key})[/]")

    async def _cmd_projects_extra(self, sub: str, args: list) -> None:
        """The project-management subcommands the desktop Project page has: rename, set the
        lead ('answers as'), add/remove members, and list/resume a project's chats."""
        if sub == "rename":
            pid = args[0] if args else await self._pick_project("rename which project")
            if not pid:
                console.print("[dim]cancelled[/]"); return
            name = " ".join(args[1:]).strip() or (await asyncio.to_thread(
                console.input, "[dim]new name:[/] ")).strip()
            if not name:
                console.print("[dim]cancelled[/]"); return
            resp = await self.request("projects.rename", {"id": pid, "name": name})
            console.print(f"[{LIME}]renamed[/] {pid} → {name!r}"
                          if resp.get("ok") else f"[error]{resp.get('error', 'failed')}[/]")
        elif sub == "lead":
            pid = args[0] if args else (self.project_id or await self._pick_project("set lead for which project"))
            if not pid:
                console.print("[dim]cancelled[/]"); return
            aid = await self._pick_agent("answers as (project lead)")
            if aid is None:
                console.print("[dim]cancelled[/]"); return
            resp = await self.request("projects.setLead", {"id": pid, "agentId": aid})
            console.print(f"[{LIME}]lead[/] {pid} → [bold]{aid}[/]"
                          if resp.get("ok") else f"[error]{resp.get('error', 'failed')}[/]")
        elif sub in ("member", "members"):
            action = args[0] if args else ""
            if action not in ("add", "rm", "remove"):
                console.print("[dim]usage: /projects member add|rm [agent][/]"); return
            pid = self.project_id or await self._pick_project("manage members of which project")
            if not pid:
                console.print("[dim]cancelled[/]"); return
            aid = args[1] if len(args) > 1 else await self._pick_agent("member agent")
            if not aid:
                console.print("[dim]cancelled[/]"); return
            method = "projects.addMember" if action == "add" else "projects.removeMember"
            resp = await self.request(method, {"id": pid, "agentId": aid})
            mark = "→" if action == "add" else "⨯"
            console.print(f"[{LIME}]{action}[/] {aid} {mark} {pid}"
                          if resp.get("ok") else f"[error]{resp.get('error', 'failed')}[/]")
        elif sub == "chats":
            pid = args[0] if args else self.project_id
            if not pid:
                pid = await self._pick_project("show chats of which project")
            if not pid:
                console.print("[dim]cancelled[/]"); return
            await self._resume_from_project(pid)

    async def _resume_from_project(self, pid: str) -> None:
        try:
            payload = await self.request("sessions.list", {"projectId": pid})
        except RuntimeError as e:
            console.print(f"[error]{e}[/]"); return
        rows = payload.get("sessions") or []
        if not rows:
            console.print(f"[dim]no chats in {pid} yet[/]"); return
        if not picker.can_pick(console):
            for i, s in enumerate(rows, 1):
                console.print(f"  [{LIME}]{i}[/] [bold]{s.get('title') or s['sessionId']}[/] "
                              f"[dim]{s.get('agentId', '?')}[/]")
            return
        opts = [picker.Option(
            value=s["sessionId"], label=s.get("title") or s["sessionId"],
            detail=f"{s.get('agentId', '?')} · {whatsapp_when(s.get('modified') or 0)}",
            current=s["sessionId"] == self.session_key) for s in rows]
        chosen = await self._pick(f"chats in {pid} ({len(rows)})", opts)
        if chosen is None:
            console.print("[dim]cancelled[/]"); return
        row = next((s for s in rows if s["sessionId"] == chosen), {})
        self.agent_id = row.get("agentId") or self.agent_id
        self.project_id = pid
        self.session_key = chosen
        console.print(f"[{LIME}]resumed:[/] [bold]{chosen}[/] [dim](agent {self.agent_id} · project {pid})[/]")

    async def _cmd_agent_info(self, args: list) -> None:
        """Agent detail (desktop's Agent page): identity, model, workspace, and skills split
        into the agent's OWN vs the inherited default library."""
        aid = args[0] if args else (await self._pick_agent("agent detail") or self.agent_id)
        try:
            d = await self.request("agents.detail", {"agentId": aid})
        except RuntimeError as e:
            console.print(f"[error]{e}[/]"); return
        if d.get("error"):
            console.print(f"[error]{d['error']}[/]"); return
        lines = [Text.from_markup(f"[bold {LIME}]{d.get('name') or aid}[/]  [dim]{d.get('id')}[/]")]
        if d.get("tagline"):
            lines.append(Text(d["tagline"], style="dim"))
        if d.get("description"):
            lines.append(Text(d["description"]))
        lines.append(Text.from_markup(
            f"[dim]model[/] {d.get('model') or '(default)'}   [dim]workspace[/] {d.get('workspace') or '—'}"))
        skills = d.get("skills") or []
        own = [s for s in skills if s.get("source") == "own"]
        shared = [s for s in skills if s.get("source") == "shared"]
        lines.append(Text(""))
        lines.append(Text.from_markup(f"[bold]skills[/] [dim]({len(own)} own · {len(shared)} inherited)[/]"))
        for s in own[:20]:
            lines.append(Text.from_markup(
                f"  • [bold]{s['name']}[/] [dim]{(s.get('description') or '')[:64]}[/]"))
        files = d.get("workspaceFiles") or []
        if files:
            lines.append(Text(""))
            lines.append(Text.from_markup(f"[bold]workspace[/] [dim]({len(files)} item(s))[/]"))
            for f in files[:12]:
                mark = "📁" if f.get("kind") == "folder" else " "
                lines.append(Text.from_markup(f"  {mark} {f['name']}"))
        console.print(Panel.fit(Group(*lines), border_style=LIME,
                                title=f"agent · {aid}", title_align="left"))

    async def _cmd_workspace(self, args: list) -> None:
        """Browse/manage the workspace of the ACTIVE project (if any) else the current agent —
        the same root the desktop Workspace tab shows. Subcommands: mkdir / rm / upload."""
        sub = args[0] if args else ""
        if sub == "mkdir":
            rel = " ".join(args[1:]).strip()
            if not rel:
                console.print("[dim]usage: /workspace mkdir <path>[/]"); return
            resp = await self.request("workspace.mkdir", self._ws_params({"path": rel}))
            console.print(f"[{LIME}]created[/] {rel}"
                          if resp.get("ok") else f"[error]{resp.get('error', 'failed')}[/]")
        elif sub in ("rm", "delete"):
            rel = " ".join(args[1:]).strip()
            if not rel:
                console.print("[dim]usage: /workspace rm <path>[/]"); return
            resp = await self.request("workspace.delete", self._ws_params({"path": rel}))
            console.print(f"[{LIME}]deleted[/] {rel}"
                          if resp.get("ok") else f"[error]{resp.get('error', 'failed')}[/]")
        elif sub == "upload":
            local = args[1] if len(args) > 1 else ""
            dest = args[2] if len(args) > 2 else ""
            if not local:
                console.print("[dim]usage: /workspace upload <localfile> [destdir][/]"); return
            path = Path(local).expanduser()
            if not path.is_file():
                console.print(f"[error]no such file: {path}[/]"); return
            import base64
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            resp = await self.request("workspace.upload",
                                      self._ws_params({"path": dest, "name": path.name, "dataBase64": data}))
            console.print(f"[{LIME}]uploaded[/] {resp.get('name')} [dim]{resp.get('path', '')}[/]"
                          if resp.get("ok") else f"[error]{resp.get('error', 'failed')}[/]")
        else:
            await self._browse_workspace("")

    async def _browse_workspace(self, rel: str) -> None:
        scope = f"project {self.project_id}" if self.project_id else f"agent {self.agent_id}"
        while True:
            try:
                resp = await self.request("workspace.list", self._ws_params({"path": rel}))
            except RuntimeError as e:
                console.print(f"[error]{e}[/]"); return
            if resp.get("error"):
                console.print(f"[error]{resp['error']}[/]"); return
            entries = resp.get("entries") or []
            here = rel or "/"
            if not picker.can_pick(console):
                console.print(f"[dim]{scope} · {here}[/]")
                for e in entries:
                    console.print(f"  {'📁' if e['kind'] == 'folder' else ' ·'} {e['name']}")
                console.print("[dim]manage: /workspace mkdir|rm <path> · upload <localfile> [dir][/]")
                return
            opts = []
            if rel:
                opts.append(picker.Option(value="..", label="..", detail="up a level"))
            for e in entries:
                mark = "📁 " if e["kind"] == "folder" else ""
                opts.append(picker.Option(value=e["rel"], label=f"{mark}{e['name']}", detail=e["kind"]))
            chosen = await self._pick(f"workspace · {scope} · {here} ({len(entries)})", opts)
            if chosen is None:
                return
            if chosen == "..":
                rel = rel.rsplit("/", 1)[0] if "/" in rel else ""
                continue
            ent = next((e for e in entries if e["rel"] == chosen), None)
            if ent and ent["kind"] == "folder":
                rel = chosen
                continue
            if ent:
                console.print(f"[{LIME}]file:[/] [bold]{ent['name']}[/] [dim]{ent['path']}[/]")
            return

    async def _cmd_capabilities(self, args: list) -> None:
        """Everything the runtime exposes — tools, plugins, skills, agents — in ONE uniform list
        (capabilities.list), the same shape the desktop reads. Optional filter: /capabilities <kind>."""
        kind = args[0].rstrip("s").lower() if args else ""     # 'agents'->'agent', 'tools'->'tool', …
        try:
            payload = await self.request("capabilities.list", {"kind": kind} if kind else {})
        except RuntimeError as e:
            console.print(f"[error]{e}[/]"); return
        rows = payload.get("capabilities") or []
        if not rows:
            console.print(f"[dim]nothing to show{f' for {kind}' if kind else ''}[/]"); return
        groups: dict = {}
        for r in rows:
            groups.setdefault(r.get("kind", "?"), []).append(r)
        for k in ("agent", "plugin", "tool", "skill"):
            items = groups.get(k) or []
            if not items:
                continue
            console.print(f"[bold {LIME}]{k}s[/] [dim]({len(items)})[/]")
            for r in sorted(items, key=lambda x: x.get("id", "")):
                desc = " ".join((r.get("description") or "").split())[:84]
                src = r.get("source") or ""
                tail = f"  [dim]· {src}[/]" if k == "tool" and src else ""
                console.print(f"  [bold]{r.get('id')}[/]" + (f"  [dim]{desc}[/]" if desc else "") + tail)

    async def _cmd_models(self, args: list) -> None:
        """Every model the runtime uses in ONE view (models.list): the brain(s) + each model-bearing
        tool, what it resolves to, its kind, and where it's set — the same map the desktop Models tab
        shows. Read-only here; edit in the desktop (kind-correct pickers) or agentd.config.json."""
        try:
            payload = await self.request("models.list", {})
        except RuntimeError as e:
            console.print(f"[error]{e}[/]"); return
        rows = payload.get("models") or []
        if not rows:
            console.print("[dim]no models[/]"); return
        table = Table(show_header=True, header_style=f"bold {LIME}", box=None, pad_edge=False)
        for col in ("what", "kind", "resolves to", "set in"):
            table.add_column(col)
        for m in rows:
            table.add_row(m.get("label", ""), m.get("kind", ""),
                          str(m.get("resolved") or "—"), m.get("configKey", ""))
        console.print(table)
        if payload.get("costEfficiency"):
            console.print("[dim]cost-efficiency ON — the brain splits into the text/image rows above[/]")
        console.print("[dim]edit in the desktop Settings → Models (kind-correct pickers), "
                      "or agentd.config.json[/]")

    async def _cmd_store(self, args: list) -> None:
        """The marketplace (desktop's Store): list bundles, install, uninstall — live, no restart."""
        sub = args[0] if args else ""
        if sub in ("install", "add", "get"):
            bid = args[1] if len(args) > 1 else ""
            if not bid:
                console.print("[dim]usage: /store install <id>[/]"); return
            console.print(f"[dim]installing {bid}…[/]")
            try:
                resp = await self.request("marketplace.install", {"id": bid})
            except RuntimeError as e:
                console.print(f"[error]{e}[/]"); return
            console.print(f"[{LIME}]installed[/] {resp.get('id')} {resp.get('version', '')}"
                          if resp.get("installed") else f"[error]{resp}[/]")
        elif sub in ("uninstall", "rm", "remove"):
            bid = args[1] if len(args) > 1 else ""
            if not bid:
                console.print("[dim]usage: /store uninstall <id>[/]"); return
            try:
                resp = await self.request("marketplace.uninstall", {"id": bid})
            except RuntimeError as e:
                console.print(f"[error]{e}[/]"); return
            console.print(f"[{LIME}]uninstalled[/] {bid}"
                          if resp.get("uninstalled") else f"[error]{resp}[/]")
        else:
            try:
                resp = await self.request("marketplace.catalog", {})
            except RuntimeError as e:
                console.print(f"[error]{e}[/]"); return
            if resp.get("error"):
                console.print(f"[dim]{resp['error']}[/]"); return
            bundles = resp.get("bundles") or []
            if not bundles:
                console.print("[dim]no bundles in the registry[/]"); return
            table = Table(show_header=True, header_style=f"bold {LIME}", box=None, pad_edge=False)
            for col in ("id", "version", "price", "status"):
                table.add_column(col)
            for b in bundles:
                if not b.get("compatible"):
                    status = "[error]needs newer agentd[/]"
                elif b.get("installed"):
                    status = "[ok]installed[/]" + (" · update" if b.get("updateAvailable") else "")
                else:
                    status = ""
                table.add_row(b["id"], b.get("version", ""), b.get("price", "free"), status)
            console.print(table)
            console.print("[dim]/store install <id> · /store uninstall <id>[/]")

    async def _cmd_config(self, args: list) -> None:
        """View config (paths, effective model, provider keys) or set one scalar key. The full
        form editor is the desktop Settings tab; this is the REPL's read + quick-set."""
        try:
            data = await self.request("config.get", {})
        except RuntimeError as e:
            console.print(f"[error]{e}[/]"); return
        if args and args[0] == "set":
            if len(args) < 3:
                console.print("[dim]usage: /config set <key> <value>[/]"); return
            key, value = args[1], coerce_scalar(" ".join(args[2:]))
            try:
                resp = await self.request("config.set", {"patch": {key: value}})
            except RuntimeError as e:
                console.print(f"[error]{e}[/]"); return
            console.print(f"[{LIME}]set[/] {key} = {value!r} [dim](restart the daemon to apply)[/]"
                          if resp.get("saved") else f"[error]{resp.get('error', 'failed')}[/]")
            return
        lines = [
            Text.from_markup(f"[dim]config file[/]  {data.get('path', '?')}"),
            Text.from_markup(f"[dim]secrets file[/] {data.get('envPath', '?')}"),
            Text.from_markup(f"[dim]effective model[/] [bold]{data.get('effectiveModel', '?')}[/]"),
        ]
        provider_keys = data.get("providerKeys") or []
        if provider_keys:
            lines.append(Text.from_markup(f"[dim]provider keys set[/] {', '.join(provider_keys)}"))
        console.print(Panel.fit(Group(*lines), border_style=LIME, title="config", title_align="left"))
        console.print("[dim]scalar edit: /config set <key> <value>  ·  full editor: the desktop Settings[/]")

    async def _agents_menu(self) -> None:
        """/agents and bare /agent: pick an agent with arrow keys and switch to
        it; falls back to the printed list + `/agent <id>` outside a TTY."""
        try:
            payload = await self.request("agents.list", {})
        except RuntimeError as e:
            console.print(f"[error]{e}[/]")
            return
        agents = payload.get("agents") or []
        current = self.agent_id or self._server_default
        if picker.can_pick(console) and agents:
            opts = [picker.Option(value=a["id"], label=a["id"],
                                  detail=" · ".join(x for x in (a.get("name", ""),
                                                                a.get("tagline", "")) if x),
                                  current=a["id"] == current)
                    for a in agents]
            chosen = await self._pick("switch agent", opts)
            if chosen is None:
                console.print("[dim]cancelled[/]")
            elif chosen == current:
                console.print(f"[dim]already on[/] [bold]{current}[/]")
            else:
                self._switch_agent(chosen)
            return
        for a in agents:
            mark = f"[{LIME}]→[/]" if a["id"] == current else " "
            tag = f"  [dim]{a['tagline']}[/]" if a.get("tagline") else ""
            console.print(f"  {mark} [bold]{a['id']}[/]  [dim]{a.get('name', '')}[/]{tag}")
        console.print("[dim]switch with /agent <id> (use 'main' for the default)[/]")

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

    def _draw(self, chunk: str):
        """Render one chunk of the stream, per the current mode.

        think -> muted italic block under a header, so reasoning is visually
        distinct from the final answer, which is rendered as bright markdown.
        """
        if self._mode == "think":
            return Padding(Text(chunk, style="italic grey50"), (0, 0, 0, 2))
        return Markdown(chunk, code_theme=CODE_THEME)  # was: default monokai (rainbow)

    def _renderable(self):
        """The live region: EXACTLY ONE LINE, always.

        This is the whole trick. `Live` repaints a multi-line region by emitting cursor-up
        (`ESC[1A`) for each line, and a terminal scrolls its viewport to follow the cursor —
        so ANY region taller than one line yanks you back to the bottom 12x a second, no
        matter how small it is. A single-line region emits only erase-line (`ESC[2K`), which
        moves nothing. Measured, not assumed.

        So the pending text is previewed as one truncated, unstyled line; the real markdown
        rendering happens in _flush_complete_blocks, which prints whole blocks into ordinary
        scrollback where they are never repainted again.
        """
        tail = self._buf.rsplit("\n", 1)[-1].strip() or "…"
        return Text(
            f"▌ {tail}",
            style="grey50" if self._mode == "think" else "grey58",
            no_wrap=True,       # wrapping would make it 2 lines, and cursor-up would be back
            overflow="ellipsis",
        )

    def _ensure_live(self, mode: str) -> None:
        # Switching between thinking and answer starts a fresh region so the
        # previous block stays on screen with its own styling.
        if self._live is not None and self._mode != mode:
            self._close_live()
        if self._live is None:
            self._mode = mode
            self._buf = ""
            if mode == "think":
                console.print(Text("✻ thinking", style="grey50"))
            self._live = Live(
                console=console,
                refresh_per_second=12,
                vertical_overflow="visible",
            )
            self._live.start()

    # ---- why the streaming buffer is FLUSHED instead of accumulated ---------------
    # `Live` repaints its region by moving the cursor up and redrawing. Once the region
    # is taller than the terminal it cannot move above the top of the screen, so it emits
    # the overflow as NEW LINES at the bottom — and the terminal scrolls to follow, 12x a
    # second. That is what made scrolling up during a long answer unwinnable: nothing was
    # "auto-scrolling", the app was simply printing, and a terminal program cannot even
    # observe that you scrolled (scrollback belongs to the emulator).
    #
    # So the live region is kept SMALL: whole blocks are committed to real scrollback as
    # soon as they are complete, and only the in-progress block is repainted. Markdown
    # cannot be rendered line-by-line (a fence or list needs its whole block), so the
    # split happens at a blank line — and never inside a ``` fence.

    @staticmethod
    def _fence_open(text: str) -> bool:
        """True when an odd number of ``` fences means we are inside a code block."""
        return text.count("```") % 2 == 1

    def _flush_complete_blocks(self) -> None:
        """Commit every finished block to scrollback, leaving the tail live."""
        while True:
            split = self._buf.find("\n\n")
            if split == -1:
                break
            block, rest = self._buf[:split], self._buf[split + 2 :]
            if self._fence_open(block):
                break  # a blank line INSIDE a code fence is not a block boundary
            self._live.update(Text(""))  # clear the region before printing above it
            console.print(self._draw(block))
            self._buf = rest

        # A single block taller than the screen (a long code listing has no blank lines)
        # would grow the region right back past the viewport. Commit it early rather than
        # resume the scroll fight: close the fence, print, and reopen it for the remainder.
        limit = max(console.size.height - 4, 8)
        if self._buf.count("\n") >= limit:
            head, _, tail = self._buf.rpartition("\n")
            if self._fence_open(head):
                head += "\n```"
                tail = "```\n" + tail
            self._live.update(Text(""))
            console.print(self._draw(head))
            self._buf = tail

    def _push(self, mode: str, delta: str) -> None:
        self._ensure_live(mode)
        self._buf += delta
        if "\n" in delta:  # only a newline can complete a block — skip the scan otherwise
            self._flush_complete_blocks()
        self._live.update(self._renderable())

    def _close_live(self) -> None:
        if self._live is not None:
            # Commit the tail to scrollback, then drop the live region. _draw, NOT
            # _renderable: the latter is only the one-line preview, so printing it here
            # would replace the last block of the answer with a truncated stub.
            self._live.update(Text(""))
            if self._buf.strip():
                console.print(self._draw(self._buf))
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
        elif etype == "model_fallback":
            # The model you configured is NOT the one answering. Loud on purpose: this was a
            # log-file-only fact, and it turned an unpaid API key into days of mystery.
            self._close_live()
            console.print(
                Text(f"  ⚠ {event.get('from')} unavailable — falling back to "
                     f"{event.get('to')}", style="bold yellow")
            )
            reason = (event.get("reason") or "").strip()
            if reason:
                console.print(Text(f"    {reason[:300]}", style="dim yellow"))
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
            elif reason == "no_output":
                # Not a normal stop — the run finished having said nothing. The message
                # itself carries the diagnosis; this line stops it reading as success.
                console.print(Text("[run ended without an answer]", style="bold yellow"))
            elif reason and reason != "stop":
                console.print(Text(f"[run ended: {reason}]", style="dim"))
            # WhatsApp-style stamp: when the reply landed (dim, tucked right)
            console.print(Text(datetime.now().strftime("%H:%M"), style="grey50"),
                          justify="right")
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
            Text.from_markup(f"[dim]agent[/] [bold]{self.agent_id or 'main'}[/]  [dim]session[/] [bold]{self.session_key}[/]   [dim]·  press [/][bold {LIME}]/[/][dim] for the command menu  ·  /agents  /sessions  /projects  /delete  /new  /quit[/]"),
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
            # Honor the gateway's default agent (a flavored install may default to a specialist,
            # not 'main') so the terminal talks to the SAME agent the desktop would — unless the
            # user pinned one with --agent. From here on agent_id is always a concrete id.
            self._server_default = info.get("agentId") or "main"
            if not self.agent_id:
                self.agent_id = self._server_default
            self._print_welcome(info)
            try:
                while True:
                    # Bracket the user's query in rules, like Claude / OpenClaw.
                    console.print(Rule(style=f"dim {LIME}"))
                    prompt = f"[bold black on {LIME}] › [/] "
                    try:
                        if picker.can_pick(console) and not console.legacy_windows:
                            # raw-key prompt: "/" opens the command palette,
                            # ↑/↓ recall history, esc clears the line
                            line = await asyncio.to_thread(
                                picker.read_line, console, prompt,
                                commands=command_options(), history=self.history)
                        else:
                            line = await asyncio.to_thread(console.input, prompt)
                    except (EOFError, KeyboardInterrupt):
                        break
                    # closing rule carries the send time (WhatsApp-style, dim, right)
                    console.print(Rule(Text(datetime.now().strftime("%H:%M"), style="grey50"),
                                       style=f"dim {LIME}", align="right"))
                    line = line.strip()
                    if not line:
                        continue
                    if not self.history or self.history[-1] != line:
                        self.history.append(line)
                    # desktop-parity commands (recents / session ops / projects mgmt / workspace /
                    # agent detail / store / config) live in their own handlers to keep this chain lean
                    if line.startswith("/") and await self._extra_command(line):
                        continue
                    if line == "/quit":
                        break
                    if line == "/new":
                        self.session_key = f"term-{uuid.uuid4().hex[:8]}"
                        where = f" [dim](in project {self.project_id})[/]" if self.project_id else ""
                        console.print(f"[{LIME}]new session:[/] [bold]{self.session_key}[/]{where}")
                        continue
                    if line == "/agents":
                        await self._agents_menu()
                        continue
                    if line == "/tools" or line.startswith("/tools "):
                        # what tools are available right now — to the CURRENT agent by default,
                        # a named agent, or the whole catalog (`/tools all`).
                        parts = line.split(maxsplit=1)
                        arg = parts[1].strip() if len(parts) > 1 else ""
                        if arg in ("all", "*"):
                            params, scope = {}, "full catalog"
                        else:
                            aid = arg or self.agent_id or "main"
                            params, scope = {"agentId": aid}, f"agent {aid}"
                        try:
                            payload = await self.request("tools.list", params)
                        except RuntimeError as e:
                            console.print(f"[error]{e}[/]")
                            continue
                        tools = payload.get("tools") or []
                        console.print(f"[dim]{len(tools)} tool(s) — {scope}[/]")
                        for t in tools:
                            src = t.get("source", "")
                            tag = f" [dim]({src})[/]" if src and src != "internal" else ""
                            console.print(f"  [bold]{t.get('name', '')}[/]{tag}  [dim]{t.get('summary', '')}[/]")
                        console.print("[dim]/tools <id> for another agent · /tools all for the whole catalog[/]")
                        continue
                    if line == "/mcp" or line.startswith("/mcp "):
                        # central MCP-server registry: list / hot-add (live, no restart) / remove.
                        parts = line.split(maxsplit=2)
                        sub = parts[1] if len(parts) > 1 else "list"
                        rest = parts[2] if len(parts) > 2 else ""
                        try:
                            if sub in ("list", ""):
                                payload = await self.request("mcp.list", {})
                                servers = payload.get("servers") or []
                                console.print(f"[dim]{len(servers)} MCP server(s)[/]")
                                for s in servers:
                                    dot = f"[{LIME}]●[/]" if s.get("connected") else "[dim]○[/]"
                                    where = " ".join(s.get("command") or []) or s.get("url") or ""
                                    console.print(f"  {dot} [bold]{s['name']}[/]  [dim]{where}[/]")
                                console.print("[dim]/mcp add <name> -- <cmd…> · /mcp add <name> --url <url> · /mcp remove <name>[/]")
                            elif sub == "add":
                                toks = rest.split()
                                if not toks:
                                    console.print("[dim]usage: /mcp add <name> -- <cmd…>  |  /mcp add <name> --url <url>[/]")
                                    continue
                                params = {"name": toks[0]}
                                if "--url" in toks:
                                    i = toks.index("--url")
                                    params["url"] = toks[i + 1] if i + 1 < len(toks) else ""
                                elif "--" in toks:
                                    params["command"] = toks[toks.index("--") + 1:]
                                else:
                                    console.print("[dim]need `-- <cmd…>` (stdio) or `--url <url>` (http)[/]")
                                    continue
                                console.print(f"[dim]connecting MCP '{params['name']}'…[/]")
                                payload = await self.request("mcp.add", params)
                                if payload.get("added"):
                                    console.print(
                                        f"[{LIME}]added[/] [bold]{params['name']}[/] — "
                                        f"{len(payload.get('tools') or [])} tool(s)  "
                                        f"[dim]persisted={payload.get('persisted')}[/]")
                                else:
                                    console.print(f"[error]{payload.get('error', 'failed')}[/]")
                            elif sub in ("remove", "rm"):
                                name = rest.strip()
                                if not name and picker.can_pick(console):
                                    payload = await self.request("mcp.list", {})
                                    servers = payload.get("servers") or []
                                    if not servers:
                                        console.print("[dim]no MCP servers registered[/]")
                                        continue
                                    name = await self._pick(
                                        "remove MCP server",
                                        [picker.Option(
                                            value=s["name"], label=s["name"],
                                            detail=" ".join(s.get("command") or []) or s.get("url") or "")
                                         for s in servers])
                                    if name is None:
                                        console.print("[dim]cancelled[/]")
                                        continue
                                if not name:
                                    console.print("[dim]usage: /mcp remove <name>[/]")
                                    continue
                                payload = await self.request("mcp.remove", {"name": name})
                                console.print(
                                    f"[{LIME}]removed[/] [bold]{name}[/]" if payload.get("removed")
                                    else f"[error]{payload.get('error', 'failed')}[/]")
                            else:
                                console.print("[dim]/mcp list | add <name> -- <cmd…> | remove <name>[/]")
                        except RuntimeError as e:
                            console.print(f"[error]{e}[/]")
                        continue
                    if line.startswith("/agent-rm") or line.startswith("/agent-delete"):
                        parts = line.split(maxsplit=1)
                        target = parts[1].strip() if len(parts) > 1 else ""
                        if not target and picker.can_pick(console):
                            try:
                                payload = await self.request("agents.list", {})
                            except RuntimeError as e:
                                console.print(f"[error]{e}[/]")
                                continue
                            removable = [a for a in (payload.get("agents") or [])
                                         if a["id"] not in ("main", "default")]
                            if not removable:
                                console.print("[dim]no removable agents (cannot delete 'main')[/]")
                                continue
                            target = await self._pick(
                                "delete agent (permanent)",
                                [picker.Option(value=a["id"], label=a["id"],
                                               detail=a.get("name", ""))
                                 for a in removable])
                            if target is None:
                                console.print("[dim]cancelled[/]")
                                continue
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
                        chats = "cleared" if resp.get("sessions") else "—"
                        console.print(
                            f"[{LIME}]deleted[/] [bold]{target}[/]  "
                            f"[dim]chats={chats} · memory={resp.get('memory', 0)} rows"
                            f" · cron/ledger={cron_n} rows[/]")
                        if self.agent_id == target:        # we were on it -> hop back to the default
                            self.agent_id = self._server_default
                            self.session_key = f"term-{uuid.uuid4().hex[:8]}"
                            console.print(f"[dim]switched to {self.agent_id} (new session {self.session_key})[/]")
                        continue
                    if line == "/agent" or line.startswith("/agent "):
                        parts = line.split(maxsplit=1)
                        if len(parts) == 1:
                            if picker.can_pick(console):
                                await self._agents_menu()
                            else:
                                console.print(f"[dim]current agent:[/] [bold]{self.agent_id or 'main'}[/]"
                                              "  [dim](/agents to list, /agent <id> to switch, /agent-rm <id> to delete)[/]")
                            continue
                        self._switch_agent(parts[1].strip())
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
                        if len(parts) >= 2 and parts[1] in ("rm", "remove", "run", "on", "off"):
                            sub = parts[1]
                            tid = parts[2] if len(parts) >= 3 else None
                            if tid is None:
                                if not picker.can_pick(console):
                                    console.print(f"[dim]usage: /cron {sub} <id>[/]")
                                    continue
                                try:
                                    payload = await self.request("cron.list", {})
                                except RuntimeError as e:
                                    console.print(f"[error]{e}[/]")
                                    continue
                                if not payload.get("autonomy"):
                                    console.print("[dim]autonomy is off — set AGENTD_AUTONOMY=1[/]")
                                    continue
                                jobs = payload.get("jobs") or []
                                if not jobs:
                                    console.print("[dim]no scheduled jobs[/]")
                                    continue
                                tid = await self._pick(
                                    f"cron {sub} — pick a job",
                                    [picker.Option(
                                        value=j["id"], label=j["id"],
                                        detail=f"{j['agentId']} · {j['schedule']} · "
                                               f"{'on' if j['enabled'] else 'off'} · "
                                               f"{(j['payload'] or '')[:40]}")
                                     for j in jobs])
                                if tid is None:
                                    console.print("[dim]cancelled[/]")
                                    continue
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
                            if len(parts) < 3 and picker.can_pick(console):
                                try:
                                    payload = await self.request("notifications.list", {})
                                except RuntimeError as e:
                                    console.print(f"[error]{e}[/]")
                                    continue
                                unread = [x for x in (payload.get("notifications") or [])
                                          if not x.get("read")]
                                if not unread:
                                    console.print("[dim]nothing unread[/]")
                                    continue
                                opts = [picker.Option(value="*", label="(ack all)",
                                                      detail=f"{len(unread)} unread")]
                                opts += [picker.Option(
                                    value=x["id"], label=f"{x['kind']} · {x['agentId']}",
                                    detail=f"{x['at']} · {x['text'][:60]}")
                                    for x in unread]
                                nid = await self._pick("acknowledge notification", opts)
                                if nid is None:
                                    console.print("[dim]cancelled[/]")
                                    continue
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
                        if picker.can_pick(console):
                            # arrow-key menu over the FULL list — it scrolls and
                            # filters, so the show-N/'all' dance isn't needed here
                            chosen = await self._pick(
                                f"resume a session — agent {who} ({len(sessions)})",
                                session_options(sessions, self.session_key))
                            if chosen is None:
                                console.print("[dim]cancelled[/]")
                            else:
                                self._resume_session(chosen, sessions)
                            continue
                        # non-TTY fallback: numbered table + typed index
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
                            self._resume_session(chosen, sessions)
                        continue
                    if line == "/delete" or line.startswith("/delete "):
                        # pick (or name) a saved session and delete it — server-side, so
                        # every connected client's list updates via sessions.changed.
                        parts = line.split(maxsplit=1)
                        target = parts[1].strip() if len(parts) > 1 else ""
                        if not target:
                            params = {"agentId": self.agent_id} if self.agent_id else {}
                            try:
                                payload = await self.request("sessions.list", params)
                            except RuntimeError as e:
                                console.print(f"[error]{e}[/]")
                                continue
                            sessions = payload.get("sessions") or []
                            if not sessions:
                                console.print("[dim]no saved sessions to delete[/]")
                                continue
                            if picker.can_pick(console):
                                target = await self._pick(
                                    "delete a session — PERMANENT",
                                    session_options(sessions, self.session_key))
                            else:
                                console.print(sessions_table(sessions, self.session_key))
                                pick = await asyncio.to_thread(
                                    console.input, "[dim]delete # (blank to cancel):[/] ")
                                target = resolve_session_choice(sessions, pick)
                            if not target:
                                console.print("[dim]cancelled[/]")
                                continue
                        confirm = await asyncio.to_thread(
                            console.input,
                            f"[error]delete[/] [bold]{target}[/] [dim]permanently? (y/N):[/] ")
                        if (confirm or "").strip().lower() not in ("y", "yes"):
                            console.print("[dim]cancelled[/]")
                            continue
                        params = {"sessionKey": target}
                        if self.agent_id:
                            params["agentId"] = self.agent_id
                        try:
                            resp = await self.request("sessions.delete", params)
                        except RuntimeError as e:
                            console.print(f"[error]{e}[/]")
                            continue
                        if not resp.get("ok"):
                            console.print(f"[error]{resp.get('error', 'delete failed')}[/]")
                            continue
                        console.print(f"[{LIME}]deleted[/] [bold]{target}[/]")
                        if target == self.session_key:      # you deleted the chat you're in
                            self.session_key = f"term-{uuid.uuid4().hex[:8]}"
                            console.print(f"[dim]new session: {self.session_key}[/]")
                        continue
                    if line == "/projects" or line.startswith("/projects "):
                        # /projects            list + pick the ACTIVE project (new chats join it)
                        # /projects new <name> create · /projects rm <id> delete · /projects off
                        parts = line.split(maxsplit=2)
                        sub = parts[1] if len(parts) > 1 else ""
                        rest = parts[2] if len(parts) > 2 else ""
                        try:
                            if sub == "new":
                                if not rest.strip():
                                    console.print("[dim]usage: /projects new <name>[/]")
                                    continue
                                resp = await self.request("projects.create", {"name": rest})
                                project = resp.get("project") or {}
                                self.project_id = project.get("id")
                                self.session_key = f"term-{uuid.uuid4().hex[:8]}"
                                console.print(
                                    f"[{LIME}]created[/] [bold]{project.get('name')}[/] "
                                    f"[dim]({self.project_id}) — active; new session "
                                    f"{self.session_key}[/]")
                                continue
                            if sub in ("rm", "delete"):
                                if not rest.strip():
                                    console.print("[dim]usage: /projects rm <id>[/]")
                                    continue
                                resp = await self.request(
                                    "projects.delete", {"id": rest.strip()})
                                if resp.get("ok"):
                                    console.print(f"[{LIME}]deleted[/] {rest.strip()} "
                                                  "[dim](its chats are standalone now)[/]")
                                    if self.project_id == rest.strip():
                                        self.project_id = None
                                else:
                                    console.print(f"[error]no such project: {rest.strip()}[/]")
                                continue
                            if sub in ("off", "none"):
                                self.project_id = None
                                console.print("[dim]project cleared — new chats are standalone[/]")
                                continue
                            payload = await self.request("projects.list", {})
                        except RuntimeError as e:
                            console.print(f"[error]{e}[/]")
                            continue
                        rows = payload.get("projects") or []
                        if not rows:
                            console.print("[dim]no projects — /projects new <name>[/]")
                            continue
                        if picker.can_pick(console):
                            opts = [picker.Option(
                                value=p["id"], label=p["name"],
                                detail=f"{p['id']} · {whatsapp_when(p.get('createdAt') or 0)}",
                                current=p["id"] == self.project_id) for p in rows]
                            chosen = await self._pick("active project (new chats join it)", opts)
                            if chosen is None:
                                console.print("[dim]cancelled[/]")
                                continue
                            self.project_id = chosen
                            # answer as the project's LEAD agent (its "answers as"), like the
                            # desktop — so a project chat behaves the same from either client.
                            proj = next((p for p in rows if p["id"] == chosen), {})
                            self.agent_id = proj.get("defaultAgentId") or self._server_default
                            self.session_key = f"term-{uuid.uuid4().hex[:8]}"
                            console.print(f"[{LIME}]project:[/] [bold]{chosen}[/] "
                                          f"[dim](answers as {self.agent_id} · new session {self.session_key})[/]")
                        else:
                            for p in rows:
                                mark = f"[{LIME}]→[/]" if p["id"] == self.project_id else " "
                                console.print(f"  {mark} [bold]{p['id']}[/]  {p['name']}  "
                                              f"[dim]{whatsapp_when(p.get('createdAt') or 0)}[/]")
                            console.print("[dim]/projects new <name> · rm <id> · off — or restart "
                                          "with `agentd chat --project <id>`[/]")
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
                        if self.project_id:
                            params["projectId"] = self.project_id   # chat lives in this project
                        await self.request("chat.send", params)
                        await self.run_done.wait()
                    except RuntimeError as e:
                        self.run_done.set()
                        self._close_live()
                        console.print(f"[error]{e}[/]")
            finally:
                self._close_live()
                reader.cancel()


def _default_url() -> str:
    """No --url given: find the running daemon via the rendezvous file (which also
    carries the M2 auth token); fall back to the historical localhost default."""
    from agent_runtime import lifecycle

    info = lifecycle.find_running()
    return info.connect_url() if info is not None else "ws://127.0.0.1:8787"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="agentd terminal client")
    parser.add_argument("--url", default=None,
                        help="gateway URL (default: auto-discover via ~/.agentd/gateway.json)")
    parser.add_argument("--session", default=None, help="session key to resume")
    parser.add_argument("--agent", default=None,
                        help="agent id to talk to (default: the gateway's default agent)")
    parser.add_argument("--project", default=None,
                        help="project id — new chats in this REPL land in that project")
    args = parser.parse_args(argv)
    session_key = args.session or f"term-{uuid.uuid4().hex[:8]}"
    client = TerminalClient(args.url or _default_url(), session_key, agent_id=args.agent,
                            project_id=args.project)
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        pass
    console.print("[dim]bye[/]")


if __name__ == "__main__":
    main()

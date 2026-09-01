"""watch — tail a run's durable event log and render its play-by-play.

See what ANY run is doing — including cron / channel / heartbeat / sub-agent runs with NO
client attached — by reading <state_dir>/events/<agent>-<run>.jsonl (written when the gateway
runs with AGENTD_EVENT_LOG=1).

Usage (run from v2/):
  python -m clients.watch                  # follow the most recent run, live
  python -m clients.watch <agent>          # follow the latest run for an agent (e.g. expense-calc)
  python -m clients.watch <run_id>         # a specific run
  python -m clients.watch <target> --no-follow   # print what's there and exit
  python -m clients.watch --list           # list recent runs and exit
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:  # the renderer uses ▶ ⚙ ⎿ etc. — force UTF-8 so a cp1252 console/pipe never crashes
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent_runtime.config import load_config

try:
    from rich.console import Console
    from rich.text import Text
    _con = Console()

    def line(text: str, style: str = "") -> None:
        _con.print(Text(text, style=style) if style else text)

    def piece(text: str, style: str = "") -> None:
        _con.print(text, style=style or None, end="")
except Exception:  # rich missing -> plain
    def line(text: str, style: str = "") -> None:
        print(text)

    def piece(text: str, style: str = "") -> None:
        sys.stdout.write(text)
        sys.stdout.flush()


def _events_dir() -> Path:
    return Path(load_config().state_dir) / "events"


def _files(d: Path) -> list[Path]:
    try:
        return sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []


def _resolve(d: Path, target: str | None) -> Path | None:
    files = _files(d)
    if not files:
        return None
    if not target or target == "latest":
        return files[0]
    for p in files:                       # run id -> file ends with -<run_id>
        if p.stem.endswith(f"-{target}"):
            return p
    for p in files:                       # agent id -> most recent <agent>-*
        if p.name.startswith(f"{target}-"):
            return p
    return None


def _args_summary(args) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    parts = [f"{k}={str(v)[:40]}" for k, v in list(args.items())[:3]]
    return "(" + ", ".join(parts) + ")"


def _result_text(result) -> str:
    """The FULL text of a tool result (a message dict with content blocks)."""
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            return "".join(b.get("text", "") for b in content if isinstance(b, dict)).strip()
        return str(result.get("text") or "").strip()
    return str(result or "").strip()


def _result_first_line(result) -> str:
    text = _result_text(result)
    return text.splitlines()[0] if text else ""


def _render(rec: dict) -> bool:
    """Render one event record. Returns True if the run ended (agent_end)."""
    ev = rec.get("event") or {}
    t = ev.get("type")
    if t == "agent_start":
        line(f"▶ run {rec.get('runId', '')} started   [{rec.get('sessionKey', '')}]", "bold cyan")
    elif t == "message_update":
        if ev.get("kind") == "text_delta":
            piece(ev.get("delta", ""))
        elif ev.get("kind") == "thinking_delta":
            piece(ev.get("delta", ""), "grey50")
    elif t == "message_end":
        piece("\n")
    elif t == "tool_execution_start":
        line(f"\n⚙ {ev.get('toolName', '?')} {_args_summary(ev.get('args'))}", "bold yellow")
    elif t == "tool_execution_end":
        text = _result_text(ev.get("result"))          # FULL result (deep single-run view)
        if text:
            style = "red" if ev.get("isError") else "grey62"
            cap = 8000
            rows = (text[:cap]).splitlines() or [text[:cap]]
            line(f"  ⎿ {rows[0]}", style)
            for r in rows[1:]:
                line(f"     {r}", style)
            if len(text) > cap:
                line(f"     …[+{len(text) - cap} chars — full result is in the .jsonl]", "grey50")
    elif t == "subagent_event":
        child, kind = ev.get("childAgent", "?"), ev.get("kind")
        if kind == "start":
            line(f"  ▶ subagent {child} started", "grey50")
        elif kind == "tool":
            line(f"    ↳ {child} · {ev.get('tool', '?')}", "grey50")
        elif kind == "error":
            line(f"  ✗ subagent {child}: {(ev.get('detail') or 'error')[:120]}", "red")
        else:
            line(f"  ✓ subagent {child} done", "grey50")
    elif t == "continuation":
        line(f"  ↻ continue ({ev.get('reason')} #{ev.get('attempt')})", "grey50")
    elif t == "tool_progress":
        txt = ev.get("text") or ""
        if txt:
            line(f"  · {txt[:160]}", "grey50")
    elif t == "agent_end":
        err = ev.get("error")
        if err:
            line(f"■ run ended: error — {str(err)[:200]}", "bold red")
        else:
            line(f"■ run ended [{ev.get('stopReason', 'stop')}]", "bold cyan")
        return True
    return False


def _tail(path: Path, follow: bool) -> None:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        while True:
            raw = f.readline()
            if raw:
                raw = raw.strip()
                if raw:
                    try:
                        if _render(json.loads(raw)) and follow:
                            return          # run ended -> stop following
                    except json.JSONDecodeError:
                        pass
                continue
            if not follow:
                return
            time.sleep(0.3)                 # at EOF on a live file -> wait for more


def _label(path: Path) -> str:
    agent, _, run = path.stem.rpartition("-")
    return f"{agent}·{run[:6]}" if run else path.stem


def _render_compact(rec: dict, path: Path, buffers: dict, show_thinking: bool = False) -> None:
    """Compact, LABELED render for the firehose (many runs interleaved): one line per
    meaningful event; assistant text/thinking are buffered per run and flushed on message_end.
    Default flushes the response's first line; with show_thinking, the FULL thinking + response
    blocks are printed (verbose firehose)."""
    ev = rec.get("event") or {}
    t = ev.get("type")
    lbl = _label(path)
    buf = buffers.setdefault(path, {"text": [], "think": []})
    if t == "message_update":
        kind = ev.get("kind")
        if kind == "text_delta":
            buf["text"].append(ev.get("delta", ""))
        elif kind == "thinking_delta" and show_thinking:
            buf["think"].append(ev.get("delta", ""))
        return
    if t == "message_end":
        think = "".join(buf["think"]).strip()
        text = "".join(buf["text"]).strip()
        buf["think"].clear()
        buf["text"].clear()
        if show_thinking and think:
            line(f"[{lbl}] 💭 thinking:", "grey50")
            for r in think.splitlines():
                line(f"        {r}", "grey50")
        if text:
            if show_thinking:                       # verbose: full response block
                line(f"[{lbl}] 💬 {text.splitlines()[0]}")
                for r in text.splitlines()[1:]:
                    line(f"        {r}")
            else:                                   # compact: first line only
                line(f"[{lbl}] 💬 {text.splitlines()[0][:160]}")
        return
    if t == "agent_start":
        line(f"[{lbl}] ▶ started   [{rec.get('sessionKey', '')}]", "bold cyan")
    elif t == "tool_execution_start":
        line(f"[{lbl}] ⚙ {ev.get('toolName', '?')} {_args_summary(ev.get('args'))}", "bold yellow")
    elif t == "tool_execution_end":
        first = _result_first_line(ev.get("result"))
        if first:
            line(f"[{lbl}]    ⎿ {first[:160]}", "red" if ev.get("isError") else "grey62")
    elif t == "subagent_event":
        child, kind = ev.get("childAgent", "?"), ev.get("kind")
        if kind == "tool":
            msg = f"↳ {child} · {ev.get('tool', '?')}"
        elif kind == "error":
            msg = f"✗ subagent {child}: {str(ev.get('detail') or 'error')[:80]}"
        elif kind == "done":
            msg = f"✓ subagent {child} done"
        else:
            msg = f"▶ subagent {child} started"
        line(f"[{lbl}]    {msg}", "grey50")
    elif t == "continuation":
        line(f"[{lbl}] ↻ {ev.get('reason')} #{ev.get('attempt')}", "grey50")
    elif t == "tool_progress":
        if ev.get("text"):
            line(f"[{lbl}]    · {str(ev.get('text'))[:120]}", "grey50")
    elif t == "agent_end":
        buffers.pop(path, None)
        err = ev.get("error")
        if err:
            line(f"[{lbl}] ■ error — {str(err)[:160]}", "bold red")
        else:
            line(f"[{lbl}] ■ [{ev.get('stopReason', 'stop')}]", "bold cyan")


def _tail_all(d: Path, follow: bool, show_thinking: bool = False) -> None:
    """Firehose: tail EVERY run file in the events dir, live, labeled per run. Pre-existing
    files start at their END (live tail); files that appear later stream from their start."""
    positions: dict[Path, int] = {}
    buffers: dict[Path, dict] = {}
    first = True
    while True:
        for p in sorted(_files(d), key=lambda x: x.stat().st_mtime):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if p not in positions:
                # live tail (follow): skip pre-existing history, stream only new. --no-follow or a
                # newly-appeared file: read from the start so you see the whole thing.
                positions[p] = size if (first and follow) else 0
            if size < positions[p]:                       # truncated/rotated
                positions[p] = 0
            if size > positions[p]:
                try:
                    with p.open("r", encoding="utf-8", errors="replace") as f:
                        f.seek(positions[p])
                        while True:
                            raw = f.readline()
                            if not raw:
                                break
                            raw = raw.strip()
                            if raw:
                                try:
                                    _render_compact(json.loads(raw), p, buffers, show_thinking)
                                except json.JSONDecodeError:
                                    pass
                        positions[p] = f.tell()
                except OSError:
                    continue
        first = False
        if not follow:
            return
        time.sleep(0.3)


def main() -> None:
    ap = argparse.ArgumentParser(prog="watch", description="tail run event logs")
    ap.add_argument("target", nargs="?", help="agent id, run id, or 'latest' (default: latest)")
    ap.add_argument("--all", action="store_true",
                    help="FIREHOSE: tail ALL runs live, labeled (full thinking + responses)")
    ap.add_argument("--compact", "-c", action="store_true",
                    help="with --all: terse scan view — one line per event, no thinking/full text")
    ap.add_argument("--no-follow", action="store_true", help="print what's there and exit")
    ap.add_argument("--list", action="store_true", help="list recent runs and exit")
    args = ap.parse_args()

    d = _events_dir()
    if args.all:
        line(f"— watching ALL runs in {d} —  (Ctrl-C to stop)", "grey50")
        try:
            _tail_all(d, follow=not args.no_follow, show_thinking=not args.compact)
        except KeyboardInterrupt:
            pass
        return
    if args.list or args.target == "list":
        files = _files(d)
        if not files:
            line(f"no event logs in {d}  (set AGENTD_EVENT_LOG=1 and restart the gateway)", "grey50")
            return
        for p in files[:30]:
            line(f"  {p.stem}", "grey62")
        return

    path = _resolve(d, args.target)
    if path is None:
        line(f"no matching run in {d}  (set AGENTD_EVENT_LOG=1 and restart the gateway)", "grey50")
        return
    line(f"— watching {path.name} —", "grey50")
    try:
        _tail(path, follow=not args.no_follow)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""Arrow-key list picker + prompt reader for the terminal client
(Claude-Code-style menus).

`pick()` draws a scrollable, filterable menu with rich.Live and reads raw
keys: ↑/↓ move (wrap-around), PgUp/PgDn jump, Home/End, Enter selects,
Esc cancels, and any printable character narrows the list (type-to-filter).

`read_line()` is the raw-key replacement for console.input at the chat
prompt: pressing "/" on an empty line pops the command palette, ↑/↓ recall
input history, Esc clears the line.

Both are blocking — call them from async code via `asyncio.to_thread`.

Callers must gate on `can_pick(console)` and keep their old typed-index flow
as the fallback for pipes, tests and dumb terminals.

The pure helpers (`filter_options`, `window_bounds`, `render_menu`) are kept
free of terminal I/O so they can be unit-tested without a real keyboard.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from rich.cells import cell_len
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

# Same signature lime as the rest of the client (see __main__.LIME).
ACCENT = "#a6e22e"

# Max option rows on screen at once; longer lists scroll around the cursor.
HEIGHT = 12


@dataclass
class Option:
    value: object          # what pick() returns when this row is chosen
    label: str             # primary text (id / name)
    detail: str = ""       # dim secondary text (msgs, dates, schedule, …)
    current: bool = False  # draws "← current" and is the initial cursor row


def can_pick(console: Console) -> bool:
    """Interactive menus need a real keyboard AND a real terminal; callers
    fall back to their typed-index flow otherwise."""
    try:
        return sys.stdin.isatty() and console.is_terminal
    except (AttributeError, ValueError, OSError):
        return False


def filter_options(options: list[Option], query: str) -> list[Option]:
    """Case-insensitive substring match over label + detail."""
    q = query.strip().lower()
    if not q:
        return list(options)
    return [o for o in options if q in o.label.lower() or q in o.detail.lower()]


def window_bounds(n: int, idx: int, height: int = HEIGHT) -> tuple[int, int]:
    """[start, end) of the visible slice, keeping idx roughly centered."""
    if n <= height:
        return 0, n
    start = max(0, min(idx - height // 2, n - height))
    return start, start + height


def render_menu(title: str, shown: list[Option], idx: int, query: str) -> Group:
    lines: list[Text] = [Text(title, style=f"bold {ACCENT}")]
    if query:
        lines.append(Text(f"  filter: {query}", style=f"italic {ACCENT}"))
    if not shown:
        lines.append(Text("  (no matches — backspace to clear the filter)", style="dim"))
    else:
        start, end = window_bounds(len(shown), idx)
        if start > 0:
            lines.append(Text(f"  ↑ {start} more", style="dim"))
        for i in range(start, end):
            o = shown[i]
            row = Text()
            if i == idx:
                row.append(" ❯ ", style=f"bold {ACCENT}")
                row.append(o.label, style=f"bold {ACCENT}")
            else:
                row.append("   ")
                row.append(o.label)
            if o.detail:
                row.append(f"  {o.detail}", style="dim")
            if o.current:
                row.append("  ← current", style=f"dim {ACCENT}")
            lines.append(row)
        if end < len(shown):
            lines.append(Text(f"  ↓ {len(shown) - end} more", style="dim"))
    lines.append(Text("  ↑↓ move · enter select · esc cancel · type to filter", style="dim"))
    return Group(*lines)


if sys.platform == "win32":

    def read_key() -> str:
        """Normalized key name ('up', 'enter', 'esc', …) or a literal char."""
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # extended key: a second code follows
            return {
                "H": "up", "P": "down", "K": "left", "M": "right",
                "I": "pgup", "Q": "pgdn", "G": "home", "O": "end",
                "S": "delete",
            }.get(msvcrt.getwch(), "")
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x1b":
            return "esc"
        if ch in ("\x08", "\x7f"):
            return "backspace"
        if ch == "\x03":
            return "ctrl-c"
        return ch

else:

    def read_key() -> str:
        """Normalized key name ('up', 'enter', 'esc', …) or a literal char."""
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                return "enter"
            if ch in ("\x08", "\x7f"):
                return "backspace"
            if ch == "\x03":
                return "ctrl-c"
            if ch != "\x1b":
                return ch
            # Bare Esc vs the start of an escape sequence: peek briefly.
            if not select.select([sys.stdin], [], [], 0.03)[0]:
                return "esc"
            if sys.stdin.read(1) != "[":
                return "esc"
            code = sys.stdin.read(1)
            if code.isdigit():  # e.g. "5~" (PgUp) / "6~" (PgDn)
                sys.stdin.read(1)  # consume the trailing "~"
                return {"1": "home", "3": "delete", "4": "end",
                        "5": "pgup", "6": "pgdn"}.get(code, "")
            return {
                "A": "up", "B": "down", "C": "right", "D": "left",
                "H": "home", "F": "end",
            }.get(code, "")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def pick(console: Console, title: str, options: list[Option]):
    """Run the menu; return the chosen Option's .value, or None on cancel.

    The menu is transient — it disappears once a choice is made so the caller
    can print a one-line result in its place.
    """
    if not options:
        return None
    idx = next((i for i, o in enumerate(options) if o.current), 0)
    query = ""
    try:
        with Live(console=console, transient=True, auto_refresh=False) as live:
            while True:
                shown = filter_options(options, query)
                idx = max(0, min(idx, len(shown) - 1)) if shown else 0
                live.update(render_menu(title, shown, idx, query), refresh=True)
                key = read_key()
                if key in ("esc", "ctrl-c"):
                    return None
                if key == "enter":
                    if shown:
                        return shown[idx].value
                elif key == "up" and shown:
                    idx = (idx - 1) % len(shown)
                elif key == "down" and shown:
                    idx = (idx + 1) % len(shown)
                elif key == "pgup":
                    idx = max(0, idx - HEIGHT)
                elif key == "pgdn":
                    idx = min(len(shown) - 1, idx + HEIGHT) if shown else 0
                elif key == "home":
                    idx = 0
                elif key == "end":
                    idx = len(shown) - 1 if shown else 0
                elif key == "backspace":
                    if query:
                        query = query[:-1]
                        idx = 0
                elif key and len(key) == 1 and key.isprintable():
                    query += key
                    idx = 0
    except KeyboardInterrupt:
        return None


def wrapped_rows(total_cells: int, width: int) -> int:
    """Terminal rows a soft-wrapped render occupies. An exact multiple of the
    width still ends on the previous row (terminals defer the wrap until the
    next character is written), so 2*width cells is 2 rows, not 3."""
    if width <= 0:
        return 1
    return max(0, total_cells - 1) // width + 1


def read_line(console: Console, prompt_markup: str, *,
              commands: list[Option] | None = None,
              history: list[str] | None = None) -> str:
    """Prompt reader with a "/" command palette (Claude-Code-style).

    - "/" as the FIRST key opens the `commands` menu: enter runs the chosen
      command, esc drops back to the prompt with "/" prefilled so the command
      (plus any arguments) can be typed manually. Mid-line "/" is literal.
    - Full line editing: ←/→ move the cursor, Home/End jump, backspace and
      Delete edit at the cursor, characters insert at the cursor.
    - ↑/↓ recall `history` (newest last), esc clears the line.
    - Raises KeyboardInterrupt on ctrl-c and EOFError on ctrl-d / ctrl-z, the
      same exceptions console.input surfaces, so callers need no new handling.

    Redraws are wrap-aware: a line that soft-wraps across N terminal rows is
    erased by moving to its origin row and clearing downward (\\x1b[J), never
    by erasing just the current row — that's what duplicated wrapped lines.
    Callers must gate on can_pick() and a non-legacy console (needs ANSI),
    keeping console.input as the fallback.
    """
    prompt = Text.from_markup(prompt_markup)
    plen = prompt.cell_len
    hist = list(history or [])
    hidx = len(hist)              # one past the end == editing a fresh line
    buf = ""
    cur = 0                       # cursor index into buf
    screen_row = 0                # render row the terminal cursor sits on now

    def clear() -> None:
        """Erase the whole render (all wrapped rows); cursor to its origin."""
        nonlocal screen_row
        if screen_row:
            console.file.write(f"\x1b[{screen_row}A")
        console.file.write("\r\x1b[J")
        screen_row = 0

    def paint() -> None:
        """Print prompt+buf, then park the terminal cursor at index `cur`."""
        nonlocal screen_row
        width = console.width or 80
        console.print(prompt, end="")
        console.file.write(buf)
        end_row = wrapped_rows(plen + cell_len(buf), width) - 1
        if cur >= len(buf):
            screen_row = end_row  # already there — printing left us at the end
        else:
            offset = plen + cell_len(buf[:cur])
            row, col = offset // width, offset % width
            if end_row > row:
                console.file.write(f"\x1b[{end_row - row}A")
            console.file.write("\r")
            if col:
                console.file.write(f"\x1b[{col}C")
            screen_row = row
        console.file.flush()

    def redraw() -> None:
        clear()
        paint()

    def finish() -> None:
        """Drop the cursor below the render before yielding the line."""
        width = console.width or 80
        end_row = wrapped_rows(plen + cell_len(buf), width) - 1
        if end_row > screen_row:
            console.file.write(f"\x1b[{end_row - screen_row}B")
        console.file.write("\n")
        console.file.flush()

    paint()
    while True:
        key = read_key()
        if key == "enter":
            finish()
            return buf
        if key == "ctrl-c":
            finish()
            raise KeyboardInterrupt
        if key in ("\x04", "\x1a"):           # ctrl-d / ctrl-z
            finish()
            raise EOFError
        if key == "backspace":
            if cur:
                buf = buf[:cur - 1] + buf[cur:]
                cur -= 1
                redraw()
        elif key == "delete":
            if cur < len(buf):
                buf = buf[:cur] + buf[cur + 1:]
                redraw()
        elif key == "left":
            if cur:
                cur -= 1
                redraw()
        elif key == "right":
            if cur < len(buf):
                cur += 1
                redraw()
        elif key == "home":
            if cur:
                cur = 0
                redraw()
        elif key == "end":
            if cur < len(buf):
                cur = len(buf)
                redraw()
        elif key == "esc":
            if buf:
                buf, cur = "", 0
                redraw()
        elif key == "up":
            if hist and hidx > 0:
                hidx -= 1
                buf = hist[hidx]
                cur = len(buf)
                redraw()
        elif key == "down":
            if hist and hidx < len(hist):
                hidx += 1
                buf = hist[hidx] if hidx < len(hist) else ""
                cur = len(buf)
                redraw()
        elif key == "/" and not buf and commands:
            # clear the prompt render; the palette draws in its place and,
            # being transient, hands the spot back when it closes
            clear()
            console.file.flush()
            chosen = pick(console, "commands", commands)
            if chosen is not None:
                buf = str(chosen)
                cur = len(buf)
                paint()                        # echo the command like typed input
                finish()
                return buf
            buf, cur = "/", 1                  # esc: type it (and args) manually
            paint()
        elif key and len(key) == 1 and key.isprintable():
            if cur == len(buf):
                # fast path: appending at the end — write the char and let the
                # terminal soft-wrap; just keep the row bookkeeping current
                buf += key
                cur += 1
                console.file.write(key)
                width = console.width or 80
                screen_row = wrapped_rows(plen + cell_len(buf), width) - 1
                console.file.flush()
            else:
                buf = buf[:cur] + key + buf[cur:]
                cur += 1
                redraw()

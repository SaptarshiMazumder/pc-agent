import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console

import agent_runtime.clients.terminal.picker as picker
from agent_runtime.clients.terminal.__main__ import COMMANDS, command_options, session_options
from agent_runtime.clients.terminal.picker import Option, filter_options, render_menu, window_bounds

OPTS = [
    Option(value="term-aaa", label="term-aaa", detail="16 msgs · 2023-11-14"),
    Option(value="term-bbb", label="term-bbb", detail="4 msgs · 2023-11-03", current=True),
    Option(value="term-ccc", label="term-ccc", detail="1 msgs"),
]


# --- pure helpers ---------------------------------------------------------


def test_filter_empty_query_returns_all():
    assert filter_options(OPTS, "") == OPTS
    assert filter_options(OPTS, "   ") == OPTS


def test_filter_matches_label_and_detail_case_insensitive():
    assert [o.value for o in filter_options(OPTS, "BBB")] == ["term-bbb"]
    assert [o.value for o in filter_options(OPTS, "16 msgs")] == ["term-aaa"]
    assert filter_options(OPTS, "zzz") == []


def test_window_fits_short_lists():
    assert window_bounds(3, 0) == (0, 3)
    assert window_bounds(3, 2) == (0, 3)


def test_window_scrolls_and_clamps():
    start, end = window_bounds(100, 0, height=10)
    assert (start, end) == (0, 10)
    start, end = window_bounds(100, 50, height=10)
    assert start <= 50 < end and end - start == 10
    start, end = window_bounds(100, 99, height=10)
    assert (start, end) == (90, 100)  # clamped at the tail


def test_render_marks_cursor_current_and_hint():
    out_console = Console(width=100, record=True)
    out_console.print(render_menu("pick one", OPTS, 0, ""))
    out = out_console.export_text()
    assert "❯ term-aaa" in out  # cursor row
    assert "← current" in out  # current marker
    assert "enter select" in out and "esc cancel" in out


def test_render_shows_filter_and_no_matches():
    out_console = Console(width=100, record=True)
    out_console.print(render_menu("pick one", [], 0, "zzz"))
    out = out_console.export_text()
    assert "filter: zzz" in out
    assert "no matches" in out


def test_render_scroll_indicators():
    many = [Option(value=i, label=f"item-{i}") for i in range(30)]
    out_console = Console(width=100, record=True)
    out_console.print(render_menu("long", many, 15, ""))
    out = out_console.export_text()
    assert "↑" in out and "more" in out  # rows hidden above and below
    assert "↓" in out


# --- pick() driven by a scripted key sequence -----------------------------


def _drive(keys, options, monkeypatch):
    seq = iter(keys)
    monkeypatch.setattr(picker, "read_key", lambda: next(seq))
    return picker.pick(Console(width=100, record=True), "t", options)


def test_pick_enter_selects_and_starts_on_current(monkeypatch):
    # cursor starts on the `current` option (index 1)
    assert _drive(["enter"], OPTS, monkeypatch) == "term-bbb"


def test_pick_arrows_move_with_wraparound(monkeypatch):
    assert _drive(["down", "enter"], OPTS, monkeypatch) == "term-ccc"
    assert _drive(["down", "down", "enter"], OPTS, monkeypatch) == "term-aaa"  # wrap
    assert _drive(["up", "enter"], OPTS, monkeypatch) == "term-aaa"


def test_pick_esc_and_ctrl_c_cancel(monkeypatch):
    assert _drive(["esc"], OPTS, monkeypatch) is None
    assert _drive(["down", "ctrl-c"], OPTS, monkeypatch) is None


def test_pick_type_to_filter_then_select(monkeypatch):
    assert _drive(["c", "c", "enter"], OPTS, monkeypatch) == "term-ccc"


def test_pick_backspace_clears_filter(monkeypatch):
    # "z" matches nothing; backspace restores the list with the cursor reset
    # to the top row (filter edits always re-home the cursor)
    assert _drive(["z", "backspace", "enter"], OPTS, monkeypatch) == "term-aaa"


def test_pick_enter_on_no_matches_is_ignored(monkeypatch):
    assert _drive(["z", "enter", "esc"], OPTS, monkeypatch) is None


def test_pick_empty_options_returns_none():
    assert picker.pick(Console(width=100, record=True), "t", []) is None


# --- read_line: prompt with "/" command palette -----------------------------

PALETTE = [Option(value="/sessions", label="/sessions", detail="resume")]


def _read(keys, monkeypatch, *, commands=None, history=None):
    seq = iter(keys)
    monkeypatch.setattr(picker, "read_key", lambda: next(seq))
    con = Console(file=io.StringIO(), width=100)
    return picker.read_line(con, "> ", commands=commands, history=history)


def test_read_line_returns_typed_text(monkeypatch):
    assert _read(["h", "i", "enter"], monkeypatch) == "hi"


def test_read_line_backspace_edits(monkeypatch):
    assert _read(["h", "i", "backspace", "enter"], monkeypatch) == "h"


def test_read_line_esc_clears(monkeypatch):
    assert _read(["h", "i", "esc", "o", "k", "enter"], monkeypatch) == "ok"


def test_read_line_slash_opens_palette_and_returns_choice(monkeypatch):
    calls = []
    monkeypatch.setattr(picker, "pick", lambda c, t, o: calls.append(o) or "/sessions")
    assert _read(["/"], monkeypatch, commands=PALETTE) == "/sessions"
    assert calls == [PALETTE]


def test_read_line_palette_esc_prefills_slash(monkeypatch):
    monkeypatch.setattr(picker, "pick", lambda c, t, o: None)
    # esc out of the palette, then finish the command by hand
    assert _read(["/", "n", "e", "w", "enter"], monkeypatch, commands=PALETTE) == "/new"


def test_read_line_midline_slash_is_literal(monkeypatch):
    monkeypatch.setattr(
        picker, "pick", lambda c, t, o: pytest.fail("palette must not open mid-line")
    )
    assert _read(["a", "/", "b", "enter"], monkeypatch, commands=PALETTE) == "a/b"


def test_read_line_history_recall(monkeypatch):
    hist = ["one", "two"]
    assert _read(["up", "enter"], monkeypatch, history=hist) == "two"
    assert _read(["up", "up", "enter"], monkeypatch, history=hist) == "one"
    assert _read(["up", "down", "enter"], monkeypatch, history=hist) == ""


def test_read_line_ctrl_c_and_eof_raise(monkeypatch):
    with pytest.raises(KeyboardInterrupt):
        _read(["ctrl-c"], monkeypatch)
    with pytest.raises(EOFError):
        _read(["\x04"], monkeypatch)


def test_wrapped_rows_counts_soft_wrapped_lines():
    assert picker.wrapped_rows(0, 80) == 1
    assert picker.wrapped_rows(1, 80) == 1
    assert picker.wrapped_rows(80, 80) == 1  # exact multiple: wrap is deferred
    assert picker.wrapped_rows(81, 80) == 2
    assert picker.wrapped_rows(160, 80) == 2
    assert picker.wrapped_rows(161, 80) == 3
    assert picker.wrapped_rows(10, 0) == 1  # degenerate width never crashes


def test_read_line_left_arrow_and_backspace_edit_at_cursor(monkeypatch):
    assert _read(["a", "b", "c", "left", "backspace", "enter"], monkeypatch) == "ac"


def test_read_line_insert_at_cursor(monkeypatch):
    assert _read(["a", "c", "left", "b", "enter"], monkeypatch) == "abc"


def test_read_line_delete_key_removes_under_cursor(monkeypatch):
    assert _read(["a", "b", "c", "home", "delete", "enter"], monkeypatch) == "bc"


def test_read_line_home_end_jump(monkeypatch):
    assert _read(["b", "c", "home", "a", "end", "d", "enter"], monkeypatch) == "abcd"


def test_read_line_cursor_clamps_at_edges(monkeypatch):
    # extra left at the start and right at the end must be no-ops
    assert _read(["left", "a", "right", "right", "b", "enter"], monkeypatch) == "ab"


def test_read_line_redraw_clears_wrapped_rows(monkeypatch):
    # a buffer 3x the console width wraps to 4 rows (incl. prompt); backspace
    # must climb to the render origin and erase downward, not just one row
    out = io.StringIO()
    con = Console(file=out, width=20)
    keys = list("x" * 60) + ["backspace", "enter"]
    seq = iter(keys)
    monkeypatch.setattr(picker, "read_key", lambda: next(seq))
    assert picker.read_line(con, "> ", commands=None) == "x" * 59
    text = out.getvalue()
    assert "\x1b[3A" in text  # cursor-up over the wrapped rows
    assert "\x1b[J" in text  # erase-down, not erase-line
    assert "\x1b[2K" not in text  # the old buggy single-row erase


def test_command_options_cover_all_commands():
    opts = command_options()
    assert [o.value for o in opts] == [c for c, _ in COMMANDS]
    assert all(o.detail for o in opts)  # every command has a description


# --- session_options bridge ------------------------------------------------


def test_session_options_maps_sessions_to_menu_rows():
    sessions = [
        {"sessionId": "term-aaa", "messages": 16, "modified": 1_700_000_000},
        {"sessionId": "term-bbb", "messages": 4},
    ]
    opts = session_options(sessions, current="term-bbb")
    assert [o.value for o in opts] == ["term-aaa", "term-bbb"]
    assert opts[0].detail.startswith("term-aaa · 16 msgs · ")  # id · msgs · when
    assert opts[1].detail == "term-bbb · 4 msgs"  # no timestamp -> no trailing when
    assert opts[1].current and not opts[0].current

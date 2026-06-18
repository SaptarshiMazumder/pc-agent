"""Pure helpers for the computer tool: coordinate mapping, key mapping, action
classification, and function-call parsing. No pyautogui / IO here so this is
unit-testable without moving the mouse.

Gemini's computer-use model emits actions with coordinates on a NORMALIZED
0-1000 grid (relative to the screenshot it was shown). `to_real` maps those onto
real screen pixels within the captured region.
"""

from __future__ import annotations

# Gemini key_combination names ("Control+A") -> pyautogui names. (From v1.)
KEY_MAP = {
    "control": "ctrl", "ctrl": "ctrl", "alt": "alt", "option": "alt",
    "shift": "shift", "cmd": "command", "command": "command", "super": "win",
    "win": "win", "meta": "win", "enter": "enter", "return": "enter",
    "tab": "tab", "escape": "esc", "esc": "esc", "backspace": "backspace",
    "delete": "delete", "space": "space", "up": "up", "down": "down",
    "left": "left", "right": "right", "pageup": "pageup", "pagedown": "pagedown",
    "home": "home", "end": "end",
}

# Actions that change machine state (vs. read-only move/scroll/wait). Used for
# the safety classification / future approval gate.
MUTATING_ACTIONS = {
    "click_at", "type_text_at", "key_combination", "drag_and_drop",
    "open_web_browser", "open_browser", "navigate",
}


def to_pyautogui_key(token: str) -> str:
    return KEY_MAP.get(token.strip().lower(), token.strip().lower())


def to_real(x: float, y: float, region: tuple[int, int, int, int]) -> tuple[int, int]:
    """Normalized 0-1000 (over the captured region) -> absolute screen pixel."""
    left, top, width, height = region
    return int(left + x / 1000 * width), int(top + y / 1000 * height)


def parse_function_call(fc) -> tuple[str, dict]:
    """Extract (name, args) from a google-genai FunctionCall (or any object with
    .name / .args). Args are coerced to a plain dict."""
    name = getattr(fc, "name", "") or ""
    raw = getattr(fc, "args", None) or {}
    try:
        args = dict(raw)
    except (TypeError, ValueError):
        args = {}
    return name, args

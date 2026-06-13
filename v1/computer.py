"""
The executor. Turns the model's tool calls into real actions on THIS machine.

  computer  -> mouse / keyboard / screenshots via pyautogui
  bash      -> shell commands via subprocess
  editor    -> read/write files

Coordinate handling: the model thinks in a fixed MODEL_DISPLAY_W x MODEL_DISPLAY_H
space. Your real screen is probably bigger. We screenshot the real screen, downscale
to the model space before sending, and upscale the coordinates it returns before
clicking. Keeping the model space small (~1280x800) noticeably improves click accuracy.
"""
import base64
import io
import subprocess
import time
import shutil
from pathlib import Path

import pyautogui
from PIL import Image

import config

pyautogui.FAILSAFE = True   # slam mouse to a corner to abort
pyautogui.PAUSE = 0.15

REAL_W, REAL_H = pyautogui.size()
SCALE_X = REAL_W / config.MODEL_DISPLAY_W
SCALE_Y = REAL_H / config.MODEL_DISPLAY_H

# Claude sends xdotool-style key names; pyautogui wants its own. Extend as needed.
KEY_MAP = {
    "Return": "enter", "Tab": "tab", "Escape": "esc", "BackSpace": "backspace",
    "Delete": "delete", "space": "space", "Up": "up", "Down": "down",
    "Left": "left", "Right": "right", "Page_Up": "pageup", "Page_Down": "pagedown",
    "Home": "home", "End": "end", "ctrl": "ctrl", "alt": "alt", "shift": "shift",
    "super": "win", "cmd": "command",
}


def _to_pyautogui_key(token: str) -> str:
    return KEY_MAP.get(token, token.lower())


def _screenshot_block() -> dict:
    """Capture real screen, downscale to model space, return as a base64 image block."""
    img = pyautogui.screenshot().resize(
        (config.MODEL_DISPLAY_W, config.MODEL_DISPLAY_H), Image.LANCZOS
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = base64.b64encode(buf.getvalue()).decode()
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": data}}


def latest_screenshot_png() -> bytes:
    """Raw PNG bytes of the current real screen — handy for posting to Discord."""
    buf = io.BytesIO()
    pyautogui.screenshot().save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- computer tool
def run_computer(inp: dict):
    action = inp.get("action")
    coord = inp.get("coordinate")

    def real(c):
        return int(c[0] * SCALE_X), int(c[1] * SCALE_Y)

    if action == "screenshot":
        return [_screenshot_block()]

    if action == "mouse_move":
        pyautogui.moveTo(*real(coord))
    elif action in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
        if coord:
            pyautogui.moveTo(*real(coord))
        button = {"right_click": "right", "middle_click": "middle"}.get(action, "left")
        clicks = {"double_click": 2, "triple_click": 3}.get(action, 1)
        pyautogui.click(button=button, clicks=clicks, interval=0.05)
    elif action == "left_click_drag":
        start = inp.get("start_coordinate")
        if start:
            pyautogui.moveTo(*real(start))
        pyautogui.dragTo(*real(coord), duration=0.4, button="left")
    elif action == "type":
        pyautogui.write(inp.get("text", ""), interval=0.01)
    elif action == "key":
        keys = [_to_pyautogui_key(k) for k in inp.get("text", "").split("+")]
        pyautogui.hotkey(*keys) if len(keys) > 1 else pyautogui.press(keys[0])
    elif action == "scroll":
        if coord:
            pyautogui.moveTo(*real(coord))
        amount = int(inp.get("scroll_amount", 3))
        direction = inp.get("scroll_direction", "down")
        pyautogui.scroll(amount * (-100 if direction == "down" else 100))
    elif action == "wait":
        time.sleep(float(inp.get("duration", 1)))
    elif action == "cursor_position":
        x, y = pyautogui.position()
        return f"cursor at ({int(x/SCALE_X)}, {int(y/SCALE_Y)})"
    else:
        return f"[unimplemented action: {action}]"

    time.sleep(0.4)            # let the UI settle before the model looks again
    return [_screenshot_block()]


# -------------------------------------------------------------------- bash tool
def run_bash(inp: dict):
    cmd = inp.get("command", "")
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True,
                             text=True, timeout=120)
        body = (out.stdout or "") + (out.stderr or "")
        return body[:6000] or "[no output]"
    except subprocess.TimeoutExpired:
        return "[command timed out after 120s]"
    except Exception as e:
        return f"[error: {e}]"


# ------------------------------------------------------------- text_editor tool
_BACKUPS: dict[str, str] = {}

def run_editor(inp: dict):
    cmd, path = inp.get("command"), inp.get("path", "")
    p = Path(path)
    try:
        if cmd == "view":
            if p.is_dir():
                return "\n".join(sorted(x.name for x in p.iterdir()))
            text = p.read_text()
            return "".join(f"{i+1}\t{ln}\n" for i, ln in enumerate(text.splitlines()))
        if cmd == "create":
            p.write_text(inp.get("file_text", ""))
            return f"created {path}"
        if cmd == "str_replace":
            text = p.read_text()
            old = inp["old_str"]
            if text.count(old) != 1:
                return f"[str_replace needs exactly one match, found {text.count(old)}]"
            _BACKUPS[path] = text
            p.write_text(text.replace(old, inp.get("new_str", "")))
            return "ok"
        if cmd == "insert":
            lines = p.read_text().splitlines(keepends=True)
            _BACKUPS[path] = "".join(lines)
            lines.insert(inp["insert_line"], inp.get("new_str", "") + "\n")
            p.write_text("".join(lines))
            return "ok"
        if cmd == "undo_edit":
            if path in _BACKUPS:
                p.write_text(_BACKUPS[path])
                return "reverted"
            return "[nothing to undo]"
        return f"[unknown editor command: {cmd}]"
    except Exception as e:
        return f"[error: {e}]"

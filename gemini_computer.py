"""
Executor for the GEMINI backend. Turns Gemini computer-use actions into real
actions on THIS machine via pyautogui.

Gemini differs from Anthropic in two ways that matter here:
  - Coordinates are NORMALIZED to a 0-1000 grid (relative to the screenshot we
    send), so we denormalize against the captured area.
  - The action vocabulary is browser-oriented (navigate / go_back / search).
    On a raw desktop we map those onto keyboard shortcuts of the focused window.

We drive a single TRACKED browser window (see window.py): screenshots capture
just that window and coordinates map onto its live rectangle, so monitor layout
and focus changes don't matter. Before a window is tracked we fall back to the
whole virtual desktop.
"""
import io
import os
import sys
import time

import pyautogui
from PIL import Image, ImageGrab

import control
import window

pyautogui.FAILSAFE = True   # slam mouse to a corner to abort
pyautogui.PAUSE = 0.15

# Virtual-desktop geometry — used ONLY as a fallback before a window is tracked.
if sys.platform.startswith("win"):
    import ctypes
    _u = ctypes.windll.user32
    VLEFT = _u.GetSystemMetrics(76)     # SM_XVIRTUALSCREEN (can be negative)
    VTOP = _u.GetSystemMetrics(77)      # SM_YVIRTUALSCREEN
    VWIDTH = _u.GetSystemMetrics(78)    # SM_CXVIRTUALSCREEN
    VHEIGHT = _u.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
else:
    VLEFT, VTOP = 0, 0
    VWIDTH, VHEIGHT = pyautogui.size()

SEND_MAX = int(os.getenv("SEND_MAX", "1600"))   # cap on the longest screenshot side

# How much the model sees / acts on:
#   "auto"    -> whole desktop UNTIL a browser is opened, then lock onto that
#                window. Best of both: sees all apps generally, focus-robust on
#                the browser once it matters. Default.
#   "window"  -> always prefer the tracked window (desktop only before one exists).
#   "desktop" -> always the whole virtual desktop, every app and monitor.
CAPTURE_MODE = os.getenv("CAPTURE_MODE", "auto").lower()

# Gemini key_combination uses names like "Control+A". Map to pyautogui names.
KEY_MAP = {
    "control": "ctrl", "ctrl": "ctrl", "alt": "alt", "option": "alt",
    "shift": "shift", "cmd": "command", "command": "command", "super": "win",
    "win": "win", "meta": "win", "enter": "enter", "return": "enter",
    "tab": "tab", "escape": "esc", "esc": "esc", "backspace": "backspace",
    "delete": "delete", "space": "space", "up": "up", "down": "down",
    "left": "left", "right": "right", "pageup": "pageup", "pagedown": "pagedown",
    "home": "home", "end": "end",
}


def _key(token: str) -> str:
    return KEY_MAP.get(token.strip().lower(), token.strip().lower())


def _desktop_rect():
    return (VLEFT, VTOP, VWIDTH, VHEIGHT)


def _use_window() -> bool:
    """True when we should drive a single tracked window rather than the desktop."""
    return CAPTURE_MODE != "desktop" and window.rect() is not None


def _active_rect():
    """The (left, top, width, height) the coordinates are normalized over."""
    return window.rect() if _use_window() else _desktop_rect()


def _capture_area():
    """Return (PIL image, rect) for the area we drive: the tracked window in
    'window' mode, or the whole virtual desktop in 'desktop' mode."""
    if _use_window():
        window.focus()                     # ensure our window is on top & unoccluded
        rect = window.rect()
        l, t, w, h = rect
        if sys.platform.startswith("win"):
            img = ImageGrab.grab(bbox=(l, t, l + w, t + h), all_screens=True)
        else:
            img = pyautogui.screenshot(region=(l, t, w, h))
        return img, rect
    if sys.platform.startswith("win"):
        img = ImageGrab.grab(all_screens=True)
    else:
        img = pyautogui.screenshot()
    return img, _desktop_rect()


def screenshot_bytes() -> bytes:
    """PNG of the tracked window (or full desktop fallback), capped at SEND_MAX on
    the longest side, aspect ratio preserved."""
    img, _ = _capture_area()
    scale = min(1.0, SEND_MAX / max(img.width, img.height))
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _real(x, y):
    """Normalized 0-1000 (over the captured area) -> absolute screen pixel."""
    l, t, w, h = _active_rect()
    return int(l + x / 1000 * w), int(t + y / 1000 * h)


# Actions that handle their own focus/launch — don't pre-focus these.
_SELF_FOCUS = {"open_web_browser", "navigate", "wait_5_seconds"}


def execute(name: str, args: dict) -> dict:
    """Run one Gemini action. Return a small result dict (empty = ok)."""
    control.check()                  # bail before physically touching the machine
    # In window mode, re-assert focus on OUR tracked window so input lands there
    # regardless of what may have stolen focus. In desktop mode we leave focus
    # alone — the model acts on whatever app it sees.
    if _use_window() and name not in _SELF_FOCUS:
        window.focus()
    try:
        if name == "open_web_browser":
            window.open_and_track("https://www.google.com")

        elif name == "click_at":
            pyautogui.click(*_real(args["x"], args["y"]))

        elif name == "hover_at":
            pyautogui.moveTo(*_real(args["x"], args["y"]))

        elif name == "type_text_at":
            pyautogui.click(*_real(args["x"], args["y"]))
            time.sleep(0.2)
            if args.get("clear_before_typing", True):
                pyautogui.hotkey("ctrl", "a")
                pyautogui.press("delete")
            pyautogui.write(args.get("text", ""), interval=0.01)
            if args.get("press_enter", False):
                pyautogui.press("enter")

        elif name == "key_combination":
            keys = [_key(k) for k in args.get("keys", "").split("+") if k.strip()]
            if len(keys) > 1:
                pyautogui.hotkey(*keys)
            elif keys:
                pyautogui.press(keys[0])

        elif name == "scroll_document":
            d = args.get("direction", "down")
            if d in ("up", "down"):
                pyautogui.scroll(600 if d == "up" else -600)
            else:
                pyautogui.hscroll(600 if d == "right" else -600)

        elif name == "scroll_at":
            pyautogui.moveTo(*_real(args["x"], args["y"]))
            mag = int(args.get("magnitude", 400))
            d = args.get("direction", "down")
            if d in ("up", "down"):
                pyautogui.scroll(mag if d == "up" else -mag)
            else:
                pyautogui.hscroll(mag if d == "right" else -mag)

        elif name == "navigate":          # focus tracked window, then address bar
            url = args.get("url", "")
            if window.have_target():
                window.focus()
            else:                          # no browser tracked yet → open & track
                window.open_and_track(url)
            pyautogui.hotkey("ctrl", "l")
            time.sleep(0.2)
            pyautogui.write(url, interval=0.01)
            pyautogui.press("enter")
            time.sleep(1.5)

        elif name == "search":            # focus the omnibox
            pyautogui.hotkey("ctrl", "l")

        elif name == "go_back":
            pyautogui.hotkey("alt", "left")

        elif name == "go_forward":
            pyautogui.hotkey("alt", "right")

        elif name == "wait_5_seconds":
            time.sleep(5)

        elif name == "drag_and_drop":
            pyautogui.moveTo(*_real(args["x"], args["y"]))
            pyautogui.dragTo(*_real(args["destination_x"], args["destination_y"]),
                             duration=0.4, button="left")
        else:
            return {"error": f"unimplemented action: {name}"}

        time.sleep(0.5)        # let the UI settle before the next screenshot
        return {}
    except Exception as e:
        return {"error": str(e)}

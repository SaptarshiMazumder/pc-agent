"""
Executor for the GEMINI backend. Turns Gemini computer-use actions into real
actions on THIS machine via pyautogui.

Gemini differs from Anthropic in two ways that matter here:
  - Coordinates are NORMALIZED to a 0-1000 grid (relative to the screenshot we
    send), so we denormalize against the real screen size.
  - The action vocabulary is browser-oriented (navigate / go_back / search).
    On a raw desktop we map those onto keyboard shortcuts of the focused window.

We keep the screenshot's aspect ratio when downscaling, otherwise normalized
coordinates would map to the wrong pixel.
"""
import io
import time
import webbrowser

import pyautogui
from PIL import Image

pyautogui.FAILSAFE = True   # slam mouse to a corner to abort
pyautogui.PAUSE = 0.15

REAL_W, REAL_H = pyautogui.size()
SEND_W = 1280               # width we downscale screenshots to (keeps payload small)

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


def screenshot_bytes() -> bytes:
    """Downscaled PNG of the real screen, aspect ratio preserved."""
    img = pyautogui.screenshot()
    h = int(SEND_W * REAL_H / REAL_W)
    img = img.resize((SEND_W, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _real(x, y):
    """Normalized 0-1000 -> real pixel coordinate."""
    return int(x / 1000 * REAL_W), int(y / 1000 * REAL_H)


def execute(name: str, args: dict) -> dict:
    """Run one Gemini action. Return a small result dict (empty = ok)."""
    try:
        if name == "open_web_browser":
            # NOT about:blank — Windows has no handler for the about: scheme and
            # pops a "find an app" dialog. A real https page launches the browser.
            webbrowser.open("https://www.google.com", new=1)
            time.sleep(2.0)

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

        elif name == "navigate":          # focus address bar, type URL, go
            pyautogui.hotkey("ctrl", "l")
            time.sleep(0.2)
            pyautogui.write(args.get("url", ""), interval=0.01)
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

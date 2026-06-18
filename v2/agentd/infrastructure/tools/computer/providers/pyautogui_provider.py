"""PyAutoGuiProvider — local OS hands+eyes via pyautogui (ported from v1
gemini_computer.py, simplified to desktop capture: no window-tracking).

Executes Gemini computer-use actions on the real machine and captures the screen.
Coordinates arrive normalized 0-1000 over the captured region and are mapped to
real pixels with `to_real`. DPI awareness is set FIRST so clicks land accurately
on high-DPI displays.
"""

from __future__ import annotations

import io
import sys
import time
import webbrowser

import pyautogui
from PIL import Image, ImageGrab

from agentd.infrastructure.tools.computer.actions import to_pyautogui_key, to_real


def _set_dpi_awareness() -> None:
    """Per-monitor-v2 DPI awareness (Windows). Without this, pyautogui.size() and
    clicks are off on scaled displays. Best-effort; no-op elsewhere."""
    if not sys.platform.startswith("win"):
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _corral_foreground_to_primary() -> None:
    """Windows, best-effort: if the foreground window sits on a NON-primary monitor,
    move it onto the primary (captured) monitor and maximize it — so the computer
    tool, which screenshots only the primary monitor, can actually see it. Windows
    places new app/browser windows wherever it likes on multi-monitor setups; this
    keeps the agent's work on the one screen it's looking at. Never raises."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        from ctypes import wintypes

        u = ctypes.windll.user32
        # 64-bit-safe handle marshalling (default restype c_int truncates HWND/HMONITOR).
        u.GetForegroundWindow.restype = ctypes.c_void_p
        u.MonitorFromWindow.restype = ctypes.c_void_p
        u.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        u.MonitorFromPoint.restype = ctypes.c_void_p
        u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]

        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return
        primary = u.MonitorFromPoint(wintypes.POINT(0, 0), 1)  # MONITOR_DEFAULTTOPRIMARY
        if u.MonitorFromWindow(hwnd, 2) == primary:            # MONITOR_DEFAULTTONEAREST
            return  # already on the captured monitor — leave it alone
        u.ShowWindow(hwnd, 9)                                  # SW_RESTORE (un-maximize so it can move)
        u.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0004)  # SWP_NOSIZE|NOZORDER -> top-left to (0,0)
        u.ShowWindow(hwnd, 3)                                  # SW_MAXIMIZE on the primary monitor
        time.sleep(0.2)                                        # let it finish before the screenshot
    except Exception:  # noqa: BLE001 — corralling must never break a run
        pass


def _virtual_rect() -> tuple[int, int, int, int]:
    if sys.platform.startswith("win"):
        import ctypes

        u = ctypes.windll.user32
        return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),  # SM_X/Y VIRTUALSCREEN
                u.GetSystemMetrics(78), u.GetSystemMetrics(79))   # SM_CX/CY VIRTUALSCREEN
    w, h = pyautogui.size()
    return (0, 0, w, h)


class PyAutoGuiProvider:
    def __init__(self, config):
        _set_dpi_awareness()
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = config.computer_pause
        self.config = config
        self.send_max = config.computer_send_max
        if config.computer_capture == "virtual":
            self.region = _virtual_rect()
            self._capture = lambda: ImageGrab.grab(all_screens=True)
            self._corral = False  # capturing all monitors already — nothing to corral
        else:  # primary monitor (default, most reliable grounding)
            w, h = pyautogui.size()
            self.region = (0, 0, w, h)
            self._capture = pyautogui.screenshot
            self._corral = getattr(config, "computer_corral_to_primary", True)

    # ---------------------------------------------------------------- eyes

    def screenshot(self) -> bytes:
        if self._corral:  # pull any off-monitor window onto the captured screen first
            _corral_foreground_to_primary()
        img = self._capture()
        scale = min(1.0, self.send_max / max(img.width, img.height))
        if scale < 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # --------------------------------------------------------------- hands

    def _real(self, x, y) -> tuple[int, int]:
        return to_real(x, y, self.region)

    def act(self, action: str, **a) -> dict:
        """Run one Gemini computer-use action. {} = ok; {"error": ...} on failure.
        FailSafeException propagates (the driver stops the run)."""
        try:
            if action == "click_at":
                pyautogui.click(*self._real(a["x"], a["y"]))
            elif action == "hover_at":
                pyautogui.moveTo(*self._real(a["x"], a["y"]))
            elif action == "type_text_at":
                pyautogui.click(*self._real(a["x"], a["y"]))
                time.sleep(0.2)
                if a.get("clear_before_typing", True):
                    pyautogui.hotkey("ctrl", "a")
                    pyautogui.press("delete")
                pyautogui.write(a.get("text", ""), interval=0.01)
                if a.get("press_enter", False):
                    pyautogui.press("enter")
            elif action == "key_combination":
                keys = [to_pyautogui_key(k) for k in a.get("keys", "").split("+") if k.strip()]
                if len(keys) > 1:
                    pyautogui.hotkey(*keys)
                elif keys:
                    pyautogui.press(keys[0])
            elif action == "scroll_document":
                d = a.get("direction", "down")
                if d in ("up", "down"):
                    pyautogui.scroll(600 if d == "up" else -600)
                else:
                    pyautogui.hscroll(600 if d == "right" else -600)
            elif action == "scroll_at":
                pyautogui.moveTo(*self._real(a["x"], a["y"]))
                mag = int(a.get("magnitude", 400))
                d = a.get("direction", "down")
                if d in ("up", "down"):
                    pyautogui.scroll(mag if d == "up" else -mag)
                else:
                    pyautogui.hscroll(mag if d == "right" else -mag)
            elif action == "drag_and_drop":
                pyautogui.moveTo(*self._real(a["x"], a["y"]))
                pyautogui.dragTo(*self._real(a["destination_x"], a["destination_y"]),
                                 duration=0.4, button="left")
            elif action == "open_browser":  # custom verb: open the default browser at a url
                webbrowser.open(a.get("url") or "https://www.google.com")
            elif action == "open_web_browser":
                webbrowser.open("https://www.google.com")
            elif action == "navigate":
                webbrowser.open(a.get("url", ""))
            elif action == "search":
                pyautogui.hotkey("ctrl", "l")
            elif action == "go_back":
                pyautogui.hotkey("alt", "left")
            elif action == "go_forward":
                pyautogui.hotkey("alt", "right")
            elif action == "wait_5_seconds":
                time.sleep(5)
            else:
                return {"error": f"unimplemented action: {action}"}
            time.sleep(0.5)  # let the UI settle before the next screenshot
            return {}
        except pyautogui.FailSafeException:
            raise
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

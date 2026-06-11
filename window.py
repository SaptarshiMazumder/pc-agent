"""
Window tracking. Instead of screenshotting the whole desktop and guessing about
monitors, we tag the ONE browser window we're driving and keep operating on it
by its handle — regardless of monitor layout or which window currently has focus.

Cross-platform via pygetwindow (ships with pyautogui). If tracking isn't
available (unsupported platform, or no window found), callers fall back to a
full-screen capture.

The tracked window's geometry is read live on every access, so moving or resizing
it mid-run is fine.
"""
import sys
import time
import webbrowser

import pyautogui

try:
    import pygetwindow as gw
except Exception:                       # pragma: no cover - platform dependent
    gw = None

_HINTS = ("Chrome", "Edge", "Firefox", "Brave", "Opera", "Chromium", "Vivaldi")
_target = None                          # the pygetwindow Window we're driving


def _browser_windows():
    if gw is None:
        return []
    seen, out = set(), []
    for kw in _HINTS:
        try:
            wins = gw.getWindowsWithTitle(kw)
        except Exception:
            wins = []
        for w in wins:
            h = getattr(w, "_hWnd", id(w))
            if w.title and h not in seen:
                seen.add(h)
                out.append(w)
    return out


def _active_browser():
    if gw is None:
        return None
    try:
        aw = gw.getActiveWindow()
    except Exception:
        return None
    if aw and aw.title and any(k in aw.title for k in _HINTS):
        return aw
    return None


def _launch(url):
    try:
        if sys.platform.startswith("win"):
            import os
            os.startfile(url)           # shell-open the default browser
        else:
            webbrowser.open(url, new=1)
    except Exception:
        webbrowser.open(url, new=1)


def open_and_track(url):
    """Open url and start tracking the resulting browser window. If a browser is
    already open (new tab instead of new window), track the active browser."""
    global _target
    before = {getattr(w, "_hWnd", id(w)) for w in _browser_windows()}
    _launch(url)

    chosen = None
    for _ in range(20):                 # up to ~6s for the window/tab to appear
        time.sleep(0.3)
        new = [w for w in _browser_windows()
               if getattr(w, "_hWnd", id(w)) not in before]
        if new:
            chosen = new[0]
            break
        ab = _active_browser()          # existing window took the new tab
        if ab:
            chosen = ab
            break
    if chosen is None:
        wins = _browser_windows()
        chosen = wins[0] if wins else None

    _target = chosen
    focus()
    return _target


def have_target() -> bool:
    return _target is not None


def rect():
    """Live (left, top, width, height) of the tracked window, or None."""
    if _target is None:
        return None
    try:
        return (_target.left, _target.top, _target.width, _target.height)
    except Exception:
        return None


def focus() -> bool:
    """Bring the tracked window to the foreground so input/capture target it.
    Re-asserting this each step is what makes us robust to focus changes."""
    if _target is None:
        return False
    try:
        if getattr(_target, "isMinimized", False):
            _target.restore()
        pyautogui.press("alt")          # release Windows' foreground lock
        _target.activate()
    except Exception:
        pass
    time.sleep(0.2)
    return True

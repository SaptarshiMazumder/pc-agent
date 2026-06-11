"""
Cooperative cancellation shared across the agent loops.

The CLI runs the agent in a worker thread and keeps the main thread free to
catch Ctrl+C. On Ctrl+C the main thread calls request_stop(), which sets a
flag; the worker calls check() at every checkpoint (before each model call,
each action, each step) and raises Cancelled to unwind promptly and cleanly.

This won't interrupt a network call already in flight — at worst the worker
finishes the current action/request (a few seconds) and then bails. For an
instant physical stop, the pyautogui corner-failsafe still applies.
"""
import threading

_STOP = threading.Event()


class Cancelled(Exception):
    """Raised at a checkpoint when cancellation was requested."""


def request_stop():
    _STOP.set()


def reset():
    _STOP.clear()


def is_stopping() -> bool:
    return _STOP.is_set()


def check():
    """Raise Cancelled if a stop was requested. Call at loop checkpoints."""
    if _STOP.is_set():
        raise Cancelled()

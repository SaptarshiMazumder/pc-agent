"""build_computer_provider — select the OS backend for the computer tool.

Returns None (tool not registered) unless the operator has explicitly enabled it
(`AGENTD_COMPUTER_ENABLED=1`) AND pyautogui is importable. The enable flag is the
real safety boundary: this tool drives the real mouse/keyboard/screen, so it is
opt-in, never auto-on.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")


def build_computer_provider(config):
    if not getattr(config, "computer_enabled", False):
        return None
    try:
        from agent_runtime.infrastructure.tools.computer.providers import PyAutoGuiProvider
    except ImportError:
        log.warning("computer tool enabled but pyautogui/Pillow not installed; disabled")
        return None
    return PyAutoGuiProvider(config)

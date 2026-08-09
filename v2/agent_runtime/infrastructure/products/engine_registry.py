"""installed_engine — read the ENGINE DISCOVERY CONTRACT that per-agent stubs depend on.

The contract is written by ``clients/desktop/build/engine-register.nsh`` when the engine installs:

    HKCU\\Software\\agentd\\Engine   (then HKLM, for a per-machine deployment)
        Exe      the executable a shortcut runs
        Path     its directory
        Version  what a payload's minimum-version floor is compared against

READING it here, in Python, is not redundant with the stub reading it in NSIS. When a per-agent app
does not start, the question is always "is the engine registered, and does the recorded file still
exist?" — and until this existed the only way to find out was regedit. A half-removed install (key
present, files gone) is the common broken state and looks identical to a working one from the
outside.

Windows-only by nature; on any other platform this reports "not applicable" rather than failing,
because the engine+stub split is a Windows installer mechanism and macOS/Linux ship differently.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ENGINE_KEY = r"Software\agentd\Engine"


@dataclass(frozen=True)
class InstalledEngine:
    exe: str = ""
    path: str = ""
    version: str = ""
    scope: str = ""  # "user" (HKCU) | "machine" (HKLM)

    @property
    def registered(self) -> bool:
        return bool(self.exe)

    @property
    def present(self) -> bool:
        """Registered AND the recorded executable is actually on disk."""
        return bool(self.exe) and Path(self.exe).is_file()


def installed_engine() -> InstalledEngine | None:
    """The engine this machine has, or None. None on non-Windows and on any registry error."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover — win32 always has it
        return None

    for root, scope in ((winreg.HKEY_CURRENT_USER, "user"), (winreg.HKEY_LOCAL_MACHINE, "machine")):
        try:
            with winreg.OpenKey(root, ENGINE_KEY) as key:

                def value(name: str) -> str:
                    try:
                        return str(winreg.QueryValueEx(key, name)[0] or "")
                    except OSError:
                        return ""

                exe = value("Exe")
                if not exe:
                    continue
                return InstalledEngine(
                    exe=exe, path=value("Path"), version=value("Version"), scope=scope
                )
        except OSError:
            continue
    return None

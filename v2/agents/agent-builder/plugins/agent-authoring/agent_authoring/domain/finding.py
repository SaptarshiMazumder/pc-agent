"""Finding — one thing a validation rule noticed about an authored agent.

A plain value: no I/O, no formatting policy, no knowledge of who will read it. Rules PRODUCE
findings; the Report decides how to render them and the tool decides whether they are fatal.

``level`` is the whole severity vocabulary and it is deliberately small:

  error  the agent is broken or will not load / will not package. Must be fixed.
  warn   it loads, but something the author wrote is being IGNORED, or will bite later
         (the classic: display keys nested inside [app] — valid TOML, silently dropped).
  info   forward-looking guidance, nothing is wrong today (the sandbox tier lives here).
"""

from __future__ import annotations

from dataclasses import dataclass

ERROR = "error"
WARN = "warn"
INFO = "info"

# Rendering order + glyph, so a Report reads worst-first without the caller sorting.
LEVEL_RANK = {ERROR: 0, WARN: 1, INFO: 2}
LEVEL_GLYPH = {ERROR: "x", WARN: "!", INFO: "i"}


@dataclass(frozen=True)
class Finding:
    level: str  # error | warn | info
    code: str  # stable machine id, e.g. "APP_TABLE_STRAY_KEYS"
    message: str  # what is wrong, in the author's terms
    path: str = ""  # the file it concerns, relative to the agent dir ("" = the agent itself)
    fix: str = ""  # the concrete corrective action, when there is an obvious one

    @property
    def is_error(self) -> bool:
        return self.level == ERROR

    def as_line(self) -> str:
        """One line for the model to read. Path first — it is what the model needs to act."""
        where = f"{self.path}: " if self.path else ""
        line = f"[{LEVEL_GLYPH.get(self.level, '?')}] {where}{self.message}"
        return f"{line}\n      fix: {self.fix}" if self.fix else line

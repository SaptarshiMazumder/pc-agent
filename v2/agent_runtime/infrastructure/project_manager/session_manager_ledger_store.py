"""SessionManagerLedgerStore — one session's ManagerLedger, as a small JSON file beside its
transcript (`<id>.jsonl` -> `<id>.manager.json`). Written atomically; a file that will not parse is
an error, not an empty ledger — silently forgetting an approved contract would let the work
restart from scratch without anyone saying so.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.domain.manager_ledger import ManagerLedger


class SessionManagerLedgerStore:
    def __init__(self, transcript_path: Path) -> None:
        self._path = Path(transcript_path).with_suffix(".manager.json")

    def load(self) -> ManagerLedger:
        if not self._path.exists():
            return ManagerLedger()
        return ManagerLedger.from_dict(json.loads(self._path.read_text(encoding="utf-8")))

    def save(self, ledger: ManagerLedger) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ledger.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)


__all__ = ["SessionManagerLedgerStore"]

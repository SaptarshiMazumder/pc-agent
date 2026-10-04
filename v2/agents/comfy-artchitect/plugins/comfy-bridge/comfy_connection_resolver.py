"""The ComfyUI this run talks to: the account's own, or the rented GPU it approved — or none yet."""

from __future__ import annotations

import json
from pathlib import Path

from account_connection_repository import OWN, RENTED, AccountConnectionRepository
from connection_value_guard import ConnectionValueGuard

#: gpu_ensure's handover file — the rented GPU's address, one per workspace (vast_bridge).
_RENTED_FILE = ".studio/connection.json"


class ComfyConnectionResolver:
    def __init__(self, root: Path, repository: AccountConnectionRepository) -> None:
        self._root = root
        self._repository = repository

    def choice(self) -> str | None:
        """rented / user_vast / user_url — or None: the person has not chosen yet."""
        record = self._repository.read()
        return record["kind"] if record else None

    def current(self) -> dict | None:
        """{kind, url, auth, portal_url?} — or None when there is nothing to talk to yet."""
        record = self._repository.read()
        if record is None:
            return None
        if record["kind"] in OWN:
            ConnectionValueGuard.check_record("the saved connection", record)
            return record
        path = self._root / _RENTED_FILE
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("url"):
            return None
        ConnectionValueGuard.check_record("the rented GPU's address", data)
        return {**data, "kind": RENTED}

    def forget_rented_address(self) -> None:
        """The choice changed: the rented GPU's address is no one's any more. Emptied, not deleted —
        a deletion inside the hosted sandbox does not reach the real workspace. gpu_ensure writes
        the address of the machine it starts."""
        path = self._root / _RENTED_FILE
        if path.is_file():
            path.write_text("{}", encoding="utf-8")

    def is_own(self) -> bool:
        """The person's own machine: nothing to rent, lease or bill for it."""
        return self.choice() in OWN

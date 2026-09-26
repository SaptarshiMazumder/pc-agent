"""ManagerLedgerStore — where one session's ManagerLedger is kept between runs."""

from __future__ import annotations

from typing import Protocol

from agent_runtime.domain.manager_ledger import ManagerLedger


class ManagerLedgerStore(Protocol):
    def load(self) -> ManagerLedger: ...

    def save(self, ledger: ManagerLedger) -> None: ...


__all__ = ["ManagerLedgerStore"]

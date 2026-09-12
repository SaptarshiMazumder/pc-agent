"""The port the instance table has to fill.

`claim` IS THE UNUSUAL ONE, and its contract is the heart of the feature: it must be impossible
for two concurrent callers to both be told "yours". An implementation that checks-then-inserts
satisfies the signature and breaks the product, because the window between the check and the
insert is exactly where a second GPU gets rented. The SQL implementation delegates that decision
to a partial unique index; any other implementation owes the same guarantee.
"""

from __future__ import annotations

from typing import Any, Protocol

from vast.domain.instance import InstanceRow


class InstanceStore(Protocol):
    """Every method takes a live connection: the caller owns the transaction, matching the way
    identity and payments do it, so schema and queries join the unit of work already in flight
    rather than racing it."""

    def live_for(self, c: Any, account_id: str) -> InstanceRow | None: ...

    def by_id(self, c: Any, row_id: str) -> InstanceRow | None: ...

    def claim(self, c: Any, account_id: str, now: float) -> tuple[InstanceRow, bool]:
        """Take this account's one live slot, or report who already holds it.

        Returns (row, mine). `mine` is True for AT MOST ONE caller — the one that must now go
        and rent. False means someone else got there first and this caller should use the row it
        was handed. See the module docstring: this is a guarantee, not a best effort.
        """
        ...

    def mark_running(self, c: Any, row_id: str, *, instance_id: int, machine_id: int,
                     hourly_usd: float, now: float) -> None:
        """Record the rental. Called the instant the marketplace returns an id — BEFORE the
        machine is reachable — so a crash after this point still leaves a row the sweeps can
        reconcile against."""
        ...

    def mark_ready(self, c: Any, row_id: str, *, url: str, now: float) -> None: ...

    def heartbeat(self, c: Any, account_id: str, *, now: float, lease_until: float = 0.0) -> bool:
        """Someone still wants this machine. A lease may only ever move FORWARD, so a late
        heartbeat cannot shorten one already held over a running render."""
        ...

    def mark_dead(self, c: Any, row_id: str, *, reason: str, now: float) -> None:
        """Free the account's slot. The row is KEPT, never deleted: `reason` is the only record
        of why a machine went away, and that is the first question after a surprising invoice."""
        ...

    def live_rows(self, c: Any) -> list[InstanceRow]:
        """Every live row, for the reaper's idle sweep."""
        ...

    def count_live(self, c: Any) -> int:
        """How many machines are live across every account — the platform-wide cap's input."""
        ...

    def spend_since(self, c: Any, account_id: str, since: float, now: float) -> float:
        """Dollars this account has run up since `since`, counting machines still running."""
        ...

    def failed_machines_since(self, c: Any, since: float) -> set[int]:
        """Machine ids of hosts that FAILED TO START since `since`, across every account — the
        ones the next rental must skip however cheap they are."""
        ...

    def record_sweep(self, c: Any, *, now: float) -> None:
        """Stamp that a reaper sweep completed — the dead-man alarm's input."""
        ...

    def last_sweep(self, c: Any) -> float:
        """When a sweep last completed; 0 = never."""
        ...

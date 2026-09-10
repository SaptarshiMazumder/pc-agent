"""What a rented GPU is, as plain data. No I/O, no HTTP, no SQL.

THE LABEL IS THE JOIN KEY, and it is the single most important idea in this module. Every
instance is stamped with `LABEL_PREFIX + <our row id>` at the moment it is rented. If this
service dies between the rental succeeding at the marketplace and the database row being
updated, that stamp is the only surviving link between a machine that is billing and the record
that was supposed to own it. Without it such an instance is invisible and runs until someone
reads an invoice.
"""

from __future__ import annotations

from dataclasses import dataclass

#: States in which an instance is, or may shortly be, costing money. The partial unique index
#: that enforces "one live instance per account" is defined over exactly this set, so the set
#: lives in one place and the index cannot drift from the queries that assume it.
LIVE_STATES = ("starting", "running")

#: Stamped onto every instance we rent. See the module docstring.
LABEL_PREFIX = "agentd-"


def label_for(row_id: str) -> str:
    return f"{LABEL_PREFIX}{row_id}"


def row_id_from_label(label: str) -> str:
    """The row id inside one of our labels, or '' if this label is not ours.

    Returning '' rather than raising is deliberate: the reaper reads labels from a marketplace
    account that may also hold machines a human started by hand, and those must be left alone
    rather than treated as errors.
    """
    text = (label or "").strip()
    return text[len(LABEL_PREFIX):] if text.startswith(LABEL_PREFIX) else ""


@dataclass(frozen=True)
class Offer:
    """One rentable machine, reduced to what the choice actually turns on."""

    offer_id: int
    machine_id: int
    gpu_name: str
    gpu_ram_mb: int
    num_gpus: int
    hourly_usd: float


@dataclass(frozen=True)
class MachineInstance:
    """A live (or dying) instance as the marketplace reports it.

    `url` is None until there is both an address and a mapped port. An instance is "running" for
    a while before it is reachable, and handing out an address that does not answer yet is worse
    than saying "not ready".
    """

    instance_id: int
    label: str
    status: str
    machine_id: int
    hourly_usd: float
    url: str | None


@dataclass(frozen=True)
class InstanceRow:
    """Our record of one rental: who it is for, and whether anyone still wants it."""

    id: str
    account_id: str
    instance_id: int | None
    machine_id: int | None
    url: str
    state: str
    hourly_usd: float
    created_at: float
    last_seen_at: float
    lease_until: float
    dead_at: float | None
    dead_reason: str

    @property
    def live(self) -> bool:
        return self.state in LIVE_STATES

    @property
    def ready(self) -> bool:
        """Usable by an agent — running AND reachable. Both, because either alone is a lie."""
        return self.state == "running" and bool(self.url)

    def idle_since(self, now: float) -> float:
        """Seconds since anything showed interest in this machine.

        A held lease means NOT IDLE regardless of contact: a submitted render is work in
        progress even while nothing is calling, and the whole point of the lease is that a long
        job must not be mistaken for an abandoned one.
        """
        if self.lease_until and now < self.lease_until:
            return 0.0
        return max(0.0, now - self.last_seen_at)

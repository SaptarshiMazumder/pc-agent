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
    #: Vast's SECURE CLOUD — a datacenter host (hosting_type 1) — as opposed to Community Cloud,
    #: which is an individual's machine at home. `verified` is a different thing entirely: the
    #: hardware-test badge, which a home rig in Bulgaria carries just as well.
    datacenter: bool = False
    #: NVIDIA compute capability x100 as Vast reports it: 750 Turing, 800/860 Ampere, 890 Ada,
    #: 900 Hopper. The one number that separates a modern card from a 2016 one, whatever the
    #: name says — which is what the GPU allowlist was doing with a list that was always short.
    compute_cap: int = 0
    #: Vast's test verdict on the machine: "verified", "unverified" (never ran it), or
    #: "deverified" (ran it and FAILED). Only the last is disqualifying on its own.
    verification: str = ""
    #: The highest CUDA the HOST'S DRIVER supports. The image is built against a CUDA version;
    #: a host whose driver is older starts the container fine and fails at the first kernel —
    #: which is how a $0.23 A5000 with CUDA 12.8 would have "rendered" on a CUDA 13 image.
    cuda_max_good: float = 0.0
    #: Free disk the host can give this rental, in GB. We ask for 60 at create; a host with
    #: less either refuses or gives less, and neither is visible until models fail to land.
    disk_gb: int = 0


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
    #: The marketplace's own words about this machine, e.g. "Preparing GPUs..." or
    #: "Error: GPU error, unable to start instance." The only place a dead host announces itself.
    status_msg: str = ""

    @property
    def dead(self) -> bool:
        """Is this machine never going to come up?

        WHY THIS IS READ FROM A MESSAGE rather than a status. A failed host sits in `created`
        forever — the same status a healthy one passes through on its way up — so status alone
        cannot tell "booting" from "broken", and polling waits out the full grace period paying
        for a machine that announced its own death in the first minute. `status_msg` is where it
        says so.

        MATCHED LOOSELY, on purpose: the exact wording is Vast's to change, and the cost of
        missing a new phrasing (wait for the reaper, as before) is much lower than the cost of
        mistaking a healthy boot for a corpse and re-renting in a loop.
        """
        return "error" in (self.status_msg or "").lower()


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
    #: THE SECRET THAT OPENS THIS MACHINE. Set as WEB_PASSWORD when it is rented — the one
    #: credential the portal's auth honours from outside — and handed to the agent as a Bearer
    #: header value. Per rental, never reused: a token that outlived its machine would open the
    #: next tenant's. Empty only on rows written before the column existed.
    auth_token: str = ""

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

"""The port a GPU marketplace has to fill.

WHY A PORT AND NOT JUST THE VAST CLIENT. Two reasons, and neither is speculative. The first is
that every test in this module drives a counting double instead of renting hardware — "how many
GPUs did that flow actually rent" is the question worth asking, and it is only askable against a
seam. The second is that Vast's own limits are the reason this design exists at all (no network
volumes, machine-pinned storage); if that forces a move to another provider, it should be one
implementation of this file, not a rewrite of the service.

Deliberately four methods. Anything richer would leak a particular marketplace's vocabulary into
the service above it, which is the coupling the port exists to prevent.
"""

from __future__ import annotations

from typing import Protocol

from vast.domain.instance import MachineInstance, Offer


class GpuMarketplace(Protocol):
    @property
    def configured(self) -> bool:
        """False when this deployment has no credentials, so the routes can answer "not
        configured" plainly instead of failing somewhere deeper."""
        ...

    def search_offers(self, *, max_hourly_usd: float, min_vram_gb: int, limit: int = 20
                      ) -> list[Offer]:
        """Rentable machines within a price ceiling, cheapest first. The ceiling is a FILTER,
        not a sort — "cheapest available" on a bad day is still whatever the market charges."""
        ...

    def create(self, offer_id: int, *, label: str, image: str, disk_gb: int, comfy_port: int,
               publish_ports: tuple = (), onstart: str = "") -> int:
        """Rent `offer_id`, stamped with `label`, and return the marketplace's instance id."""
        ...

    def destroy(self, instance_id: int) -> None:
        """Terminate an instance. MUST be idempotent — an instance already gone is success, not
        an error, because the reaper and the manual path both have to be safe to retry."""
        ...

    def get_instance(self, instance_id: int) -> MachineInstance | None:
        """One instance, or None if it is gone. How a booting machine is polled for the address
        it does not have yet."""
        ...

    def list_instances(self) -> list[MachineInstance]:
        """Everything currently on the account.

        THE SOURCE OF TRUTH FOR WHAT EXISTS. Our table records what SHOULD exist; only this call
        knows what is actually being billed, which is the difference the orphan sweep is built
        on.
        """
        ...

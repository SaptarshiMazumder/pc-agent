"""What to rent, and when to stop paying for it. One object, so nothing can disagree about it.

THESE NUMBERS ARE THE SPENDING POLICY. They are grouped rather than scattered because the idle
timeout has to mean the same thing to the endpoint that writes a heartbeat and to the sweep that
kills on one — two constants drifting apart would show up as machines that die mid-render, or
machines that never die at all.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstanceSettings:
    #: THE IMAGE, AND IT MUST BE A TAG THAT EXISTS. `vastai/comfy:latest` does not: the repo
    #: publishes 113 versioned tags and no `latest` at all, so an instance rented with it sits
    #: in `loading` FOREVER, pulling something that will never arrive, billing the whole time.
    #: Nothing errors — the marketplace rented exactly what it was asked for.
    #:
    #: That failure is invisible from our side (status "loading" is also what a legitimate
    #: multi-GB pull looks like) which is why it burned real money before anyone checked Docker
    #: Hub. Pin a real tag, and re-pin deliberately rather than reaching for a floating one.
    image: str = "vastai/comfy:v0.35.0-cuda-13.2-py312"

    #: EVERY PORT VAST'S OWN COMFYUI TEMPLATE PUBLISHES, not just ComfyUI's. Taken from that
    #: template rather than reasoned about: 1111 is the Instance Portal the image's supervisor
    #: serves, and the rest are what it expects to have. Publishing only 8188 is the sort of
    #: "we only need this one" trim that works until the thing you trimmed was load-bearing for
    #: boot. `comfy_port` is still the one an address is read back from.
    publish_ports: tuple = (1111, 8080, 8188, 8288, 8384, 10100, 10200, 72299)
    disk_gb: int = 60
    comfy_port: int = 8188

    #: A CEILING THE MARKETPLACE NEVER SEES PAST: passed as a filter on the offer search, and
    #: re-checked on the chosen offer before renting, because a filter the market ignores is not
    #: a limit. Renting is the irreversible step; it gets the second look.
    max_hourly_usd: float = 0.50
    min_vram_gb: int = 24

    #: No contact and no lease for this long, and the reaper takes it.
    idle_seconds: float = 600.0

    #: The longest one render may hold a machine against the idle timer. Capped so a crashed
    #: agent cannot keep a GPU forever by claiming to be busy — the lease is a stay of
    #: execution, not a pardon.
    max_lease_seconds: float = 1800.0

    #: How long a row may sit in 'starting' before the reaper treats it as a failed rental.
    #: Longer than any real boot, short enough that a wedged one is not paid for all day.
    #:
    #: FIRST BOOT IS GENUINELY SLOW: a multi-GB ComfyUI image was still pulling after five
    #: minutes on a real rental, where vast's small base image was serving in under one. This
    #: has to clear the slow case comfortably or the reaper kills machines that were fine.
    starting_grace_seconds: float = 900.0

    #: WHAT ONE ACCOUNT MAY SPEND PER CALENDAR MONTH, in dollars. 0 = uncapped.
    #:
    #: THE HOURLY CEILING DOES NOT DO THIS JOB. `max_hourly_usd` limits how fast money burns;
    #: it says nothing about how long. One person leaving sessions open all month stays under
    #: every per-rental limit and still runs up a bill, because the publisher's card pays for
    #: everyone. This is the limit that actually bounds that.
    monthly_cap_usd: float = 20.0

    #: HOW MANY MACHINES MAY BE RUNNING AT ONCE, across every account. 0 = unlimited.
    #:
    #: The per-account cap bounds one user; this bounds the PLATFORM, including the case the
    #: per-account cap cannot see — a hundred new accounts renting one machine each. Worst-case
    #: burn is this times max_hourly_usd, which is a number worth being able to state out loud.
    max_live_instances: int = 10

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

    #: WHAT VAST'S OWN COMFYUI TEMPLATE PASSES, minus two things. This is why ComfyUI never
    #: answered on ANY host today — the instance log said it in one line, over and over:
    #:
    #:     Skipping comfyui startup (not in /etc/portal.yaml)
    #:
    #: The image's supervisor starts only what PORTAL_CONFIG names; portal.yaml is generated
    #: from it at boot. We rented the bare image with nothing but port keys, so every machine
    #: came up with ComfyUI never started and billed for as long as it was left. Taken verbatim
    #: from template 264313 (22,667 rentals), which is what a human gets from the "ComfyUI"
    #: button. The two omissions: OPEN_BUTTON_TOKEN (Vast generates its own; we pass
    #: WEB_PASSWORD per rental instead — see auth_token) and PROVISIONING_SCRIPT, whose default
    #: downloads one multi-GB Civitai checkpoint nobody asked for at every boot; the image
    #: already ships ComfyUI-Manager, and the agent installs exactly what its workflow needs.
    container_env: tuple = (
        ("OPEN_BUTTON_PORT", "1111"),
        ("JUPYTER_DIR", "/"),
        ("DATA_DIRECTORY", "/workspace/"),
        (
            "PORTAL_CONFIG",
            "localhost:1111:11111:/:Instance Portal|localhost:8188:18188:/:ComfyUI|"
            "localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal|"
            "localhost:8384:18384:/:Syncthing",
        ),
        ("COMFYUI_ARGS", "--disable-auto-launch --port 18188 --enable-cors-header"),
    )

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
    #: THE CEILING FOLLOWS THE TIER. This was $0.50 while Community Cloud was allowed, and a
    #: community RTX 3090 at $0.14 cleared it easily. Secure Cloud (below) has nothing at that
    #: price: measured on the live market, the floor under every other filter is an A100 80GB
    #: at $1.00/hr — when it is there at all — then H100s from $2.20. A ceiling that finds
    #: nothing is not a saving, it is a user with no GPU. $3.00 admits the H100 band: four
    #: machines when this was measured, the first level that is not a coin flip.
    max_hourly_usd: float = 3.00

    #: SECURE CLOUD ONLY. Vast's datacenter tier — ISO-certified hosts, static addresses,
    #: hardware nobody is also gaming on — as opposed to Community Cloud, which is somebody's
    #: home rig and was where every host that failed to start today came from. `verified` did
    #: NOT express this: it is the hardware-test badge, and a home machine carries it too.
    secure_cloud_only: bool = True

    #: THE `verified` BADGE IS NOT REQUIRED. It is Vast's own hardware-test pass, and requiring
    #: it took a Secure Cloud pool that was already thin down to a handful: datacenter hosts
    #: often list machines that have simply never run the test. Secure Cloud is the trust
    #: decision; this badge was a second gate on top of it. A card Vast has DEverified — ran
    #: the test and failed it — is still refused, regardless of this: that is a different fact.
    require_verified: bool = False

    #: NEVER THE SAME CORPSE TWICE. A host that fails to start is released on the next poll,
    #: and the next rental is cheapest-first — which is the same host, because it is still the
    #: cheapest listing and its failure did not change its price. Seen live: machine 108820
    #: died with "GPU error", was released, and was rented again within the quarter hour. A
    #: machine that failed to start in this window is skipped, however cheap it is.
    failed_machine_cooldown_seconds: float = 24 * 3600.0
    #: 16GB, DOWN FROM 24. With Secure Cloud the 24GB floor left a pool of one machine at a
    #: time; the agent already sizes its weights to the card it probes (fp8 and quantised
    #: variants over full precision), so a 16GB datacenter card is a smaller job, not a broken
    #: one. Below 16GB the video models this agent is for do not fit at all. 0 = any.
    min_vram_gb: int = 16

    #: HOW RELIABLE THE HOST MUST BE. Vast scores every machine on whether it actually starts
    #: and stays up, and we were not looking: a rented box came back
    #: "Error: GPU error, unable to start instance" and we paid for it until the reaper's grace
    #: expired. This is the single filter that would have skipped it.
    min_reliability: float = 0.95

    #: Minimum CUDA the HOST's driver supports. The image is built against a CUDA version; a
    #: host too old to run it starts and then fails in a way that looks like our bug.
    #: BOUND TO THE IMAGE TAG ABOVE. vastai/comfy:…-cuda-13.2 ships torch cu130, which needs a
    #: CUDA 13 driver; a host reporting cuda_max_good 12.8 starts the container and fails at
    #: the first kernel. The floor was 12.4, and the next rental under it would have been a
    #: $0.23 A5000 at 12.8 — the L4 the user was rented reported 12.4. Change the image, change
    #: this.
    min_cuda: float = 13.0
    #: Mbps down. Every run pulls multi-GB weights, so a slow host is not cheap, it is a longer
    #: bill for the same work.
    min_inet_down: int = 100

    #: THE FLOOR ON HOW MODERN THE CARD IS, as a number every card reports. 750 = Turing (RTX 20xx,
    #: T4, Quadro RTX): the first generation with fp16 tensor cores, and the oldest thing worth
    #: loading a diffusion model on. This REPLACES a GPU-name allowlist that was always missing
    #: something — it had to grow by hand for the RTX PRO 6000, then the H200 — and that silently
    #: re-imposed a 24GB floor after the VRAM floor was removed, because every name on it was a
    #: 24GB+ card. Vast reports compute capability x100: 610 Pascal, 750 Turing, 800/860 Ampere,
    #: 890 Ada, 900 Hopper.
    min_compute_cap: int = 750

    #: THE ONE FAMILY A NUMBER DOES NOT CATCH. The CMP 170HX is a crypto-MINING card — no display
    #: output, crippled CUDA — and it reports compute capability 800 and "64GB", so the floor
    #: above and any VRAM floor both pass it, and it is what a cheapest-first sort rented in
    #: production at three times the price of a working RTX 3090. Substring match on gpu_name.
    gpu_denylist: tuple = ("CMP",)

    #: An optional allowlist, substring-matched on gpu_name. EMPTY = ANY, and empty is the
    #: default now: Secure Cloud plus the compute floor plus the denylist do the job a name list
    #: did, without having to be kept up to date. Set it only to pin a deployment to specific
    #: cards.
    gpu_allowlist: tuple = ()

    #: THE CARDS WORTH PAYING FOR, IN QUALITY ORDER — PC_Rent's Vast catalogue, with its speed
    #: weights kept only as an ORDER (they are Blender render speeds; the order is what carries
    #: over). Matched by longest substring so Vast's variants ("RTX PRO 6000 Max-Q") count.
    #: The picker takes the best tier the ceiling allows and the cheapest offer of that tier —
    #: not the cheapest card, which is how an L4 got rented while an RTX PRO 6000 sat at $1.74.
    #:
    #: ONE DEPARTURE FROM THEIR ORDER: the RTX PRO 6000 ranks first. Their list is ordered for
    #: Blender, where an H100 NVL edges it; this agent runs diffusion and video models, where
    #: the PRO 6000's 96GB and Blackwell generation are worth more than the H100's memory
    #: bandwidth — and on the live market it was $1.69/hr against the H100 NVL's $2.95. With
    #: their order the picker took the H100 and paid the ceiling; with this one it takes the
    #: better card for the job and leaves $1.26/hr on the table.
    #: Empty = any card, cheapest first (the old behaviour).
    gpu_catalogue: tuple = (
        ("RTX PRO 6000", 3.0),
        ("H200 NVL", 2.8), ("H100 SXM", 2.5), ("H100 NVL", 2.45),
        ("RTX 5090", 2.0), ("RTX 6000Ada", 1.85), ("A100 SXM4", 1.85), ("L40S", 1.75),
        ("A100 PCIE", 1.7), ("L40", 1.55), ("RTX 5880Ada", 1.5), ("RTX 4090", 1.45),
        ("RTX 4080S", 1.3), ("RTX A6000", 1.3), ("A40", 1.0),
    )

    #: HOW MANY OFFERS TO LOOK AT before choosing. Cheapest-first only ever needed the first
    #: twenty; a quality-first choice needs to see the whole pool under the ceiling, or the
    #: best card is simply not in the page. 200 is more than the datacenter pool has ever been.
    search_limit: int = 200

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

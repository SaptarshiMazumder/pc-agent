"""Vast.ai, as an implementation of the GpuMarketplace port — and the only place a key is used.

STDLIB ONLY. The accounts service that hosts this declares it imports "ONLY the stdlib plus
FastAPI", and a GPU marketplace client is not the reason to break that. `urllib.request`, like
accounts/admin_api.py.

WHAT IS AND IS NOT VERIFIED, because this file is where the docs stop being trustworthy.

VERIFIED against the live API:
  * `GET /bundles?q=<url-encoded json>` for offers. THE DOCUMENTED FORM DOES NOT EXIST —
    `PUT /bundles/` with `{"q": …}` answers 404 and the POST form answers 400. This is exactly
    why the adapter was probed before anything was built on it.
  * `GET /instances/` for what is running, and the offer field names below (`id`, `machine_id`,
    `gpu_name`, `gpu_ram` in MB, `num_gpus`, `dph_total`).

  * `PUT /asks/{id}/` to rent, and `DELETE /instances/{id}/` to destroy — both proved against
    real rentals, destroy included twice over (a second call on a dead instance is a no-op).
  * `ports`, which is how the address is found: `{"8188/tcp": [{"HostIp": …, "HostPort": …}]}`.

TWO THINGS THAT LOOK LIKE FAILURES AND ARE NOT, both learned the expensive way:

  * `ports` IS NULL UNTIL A CONTAINER IS ACTUALLY RUNNING. An instance reports a public IP, and
    statuses of "running"/"loading"/"created", well before anything is published. An image whose
    command exits immediately (alpine) never publishes at all. So a missing address means "not
    ready yet", never "broken" — which is exactly why `ensure` polls instead of failing.
  * SSH IS PUBLISHED ALONGSIDE. A real instance showed ComfyUI on host port 56740 and ssh on
    56899, in one map, unordered. Taking "the first entry with a host port" hands back the ssh
    endpoint — hence `_comfy_port`, and why `_url_for` matches on the internal port.

Nothing above this file depends on any of it, which is the point of the port.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from vast.domain.errors import MarketplaceError
from vast.domain.instance import MachineInstance, Offer

DEFAULT_BASE = "https://console.vast.ai/api/v0"


class VastMarketplace:
    """The four-and-a-bit operations this module needs from Vast."""

    def __init__(
        self,
        api_key: str,
        *,
        base: str = DEFAULT_BASE,
        timeout_s: float = 30.0,
        comfy_port: int = 8188,
    ) -> None:
        self._key = (api_key or "").strip()
        self._base = base.rstrip("/")
        self._timeout_s = timeout_s
        # WHICH published port is the one we want. Vast maps several (SSH among them) and
        # returns them in no particular order, so picking "the first one with a host port" hands
        # back an SSH endpoint that ComfyUI never answers on.
        self._comfy_port = int(comfy_port)

    @property
    def configured(self) -> bool:
        return bool(self._key)

    # ---------------------------------------------------------------- transport

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self._base}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._key}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as res:
                raw = res.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:  # answered, and said no
            raise MarketplaceError(
                f"{method} {path}", e.code, e.read().decode("utf-8", "replace")
            ) from e
        except urllib.error.URLError as e:  # never reached the server
            raise MarketplaceError(f"{method} {path}", 0, str(e.reason)) from e
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError as e:
            raise MarketplaceError(f"{method} {path}", 200, f"not JSON: {raw[:200]}") from e
        return parsed if isinstance(parsed, dict) else {"result": parsed}

    # ---------------------------------------------------------------- offers

    def search_offers(
        self,
        *,
        max_hourly_usd: float,
        min_vram_gb: int,
        limit: int = 20,
        min_reliability: float = 0.0,
        min_cuda: float = 0.0,
        min_inet_down: int = 0,
        gpu_allowlist: tuple = (),
    ) -> list[Offer]:
        """Rentable machines that are actually worth renting, cheapest first.

        CHEAPEST-THAT-CLEARS-A-VRAM-NUMBER IS NOT "GOOD", and the marketplace will happily prove
        it: this returned a CMP 170HX — a mining card with crippled CUDA — at three times the
        price of a working RTX 3090, and separately a host that answered
        "Error: GPU error, unable to start instance" the moment it was rented. `reliability2` is
        the filter that skips the second; the allowlist skips the first.
        """
        query = {
            "rentable": {"eq": True},
            "verified": {"eq": True},
            "external": {"eq": False},
            "num_gpus": {"eq": 1},
            "gpu_ram": {"gte": int(min_vram_gb) * 1024},
            "dph_total": {"lte": float(max_hourly_usd)},
            "order": [["dph_total", "asc"]],
            "type": "on-demand",
            "limit": int(limit),
        }
        if min_reliability:
            query["reliability2"] = {"gte": float(min_reliability)}
        if min_cuda:
            query["cuda_max_good"] = {"gte": float(min_cuda)}
        if min_inet_down:
            query["inet_down"] = {"gte": int(min_inet_down)}
        # GET WITH THE QUERY URL-ENCODED, verified against the live API. Vast's docs describe a
        # `PUT /bundles/` taking `{"q": …}`; that path 404s and the POST form 400s. This is the
        # one that answers, and it is why this adapter was probed before anything was built on
        # top of it.
        payload = self._call("GET", f"/bundles?q={urllib.parse.quote(json.dumps(query))}", None)
        offers = [
            Offer(
                offer_id=int(row.get("id") or 0),
                machine_id=int(row.get("machine_id") or 0),
                gpu_name=str(row.get("gpu_name") or ""),
                gpu_ram_mb=int(row.get("gpu_ram") or 0),
                num_gpus=int(row.get("num_gpus") or 0),
                hourly_usd=float(row.get("dph_total") or 0.0),
            )
            for row in (payload.get("offers") or [])
        ]
        # EVERY FILTER IS RE-APPLIED TO THE RESULT, not trusted from the request. A query
        # parameter the marketplace silently drops would otherwise become an unbounded bill, or
        # a mining card. The allowlist is matched here rather than in the query because Vast's
        # gpu_name is free text and an `in` clause on it misses "RTX 4090 D" and friends.
        keep = []
        for o in offers:
            if not o.offer_id or o.hourly_usd > max_hourly_usd:
                continue
            if o.gpu_ram_mb and o.gpu_ram_mb < int(min_vram_gb) * 1024:
                continue
            if gpu_allowlist and not any(
                name.lower() in o.gpu_name.lower() for name in gpu_allowlist
            ):
                continue
            keep.append(o)
        return keep

    # ---------------------------------------------------------------- lifecycle

    def create(
        self,
        offer_id: int,
        *,
        label: str,
        image: str,
        disk_gb: int,
        comfy_port: int,
        publish_ports: tuple = (),
        onstart: str = "",
    ) -> int:
        body: dict = {
            "client_id": "me",
            "image": image,
            "disk": int(disk_gb),
            "label": label,
            "runtype": "args",
            # Vast takes published ports as docker-style args in `env`, one key each.
            "env": {
                f"-p {p}:{p}": "1" for p in (publish_ports or (comfy_port,))
            },
        }
        if onstart:
            body["onstart"] = onstart
        payload = self._call("PUT", f"/asks/{int(offer_id)}/", body)
        if not payload.get("success", True):
            raise MarketplaceError("create", 200, json.dumps(payload)[:400])
        new_id = payload.get("new_contract") or payload.get("instance_id")
        if not new_id:
            # We may have rented something we cannot name — the worst outcome available, since
            # we would then pay for a box we cannot address or destroy by id. Raising is what
            # frees the slot upstream; the orphan sweep is what actually recovers it, by label.
            raise MarketplaceError(
                "create", 200, f"no instance id in reply: {json.dumps(payload)[:300]}"
            )
        return int(new_id)

    def destroy(self, instance_id: int) -> None:
        """Idempotent by contract — already gone is success, so retries are always safe."""
        try:
            self._call("DELETE", f"/instances/{int(instance_id)}/", {})
        except MarketplaceError as e:
            if e.status in (404, 422):
                return
            raise

    def get_instance(self, instance_id: int) -> MachineInstance | None:
        payload = self._call("GET", f"/instances/{int(instance_id)}/", None)
        row = payload.get("instances") or payload.get("instance")
        if isinstance(row, list):
            row = row[0] if row else None
        return self._shape(row) if isinstance(row, dict) else None

    def list_instances(self) -> list[MachineInstance]:
        payload = self._call("GET", "/instances/", None)
        return [self._shape(row) for row in (payload.get("instances") or [])]

    # ---------------------------------------------------------------- shaping

    def _shape(self, row: dict) -> MachineInstance:
        return MachineInstance(
            instance_id=int(row.get("id") or 0),
            label=str(row.get("label") or ""),
            status=str(row.get("actual_status") or row.get("cur_state") or ""),
            machine_id=int(row.get("machine_id") or 0),
            hourly_usd=float(row.get("dph_total") or 0.0),
            url=self._url_for(row),
        )

    def _url_for(self, row: dict) -> str | None:
        """The address ComfyUI answers on, or None while it has none.

        Vast gives each instance a slice of a shared public IP: an internal port is published on
        a RANDOM external one, so the address cannot be predicted at rental time and must be
        read back. The container's own PUBLIC_IPADDR is set once at boot and not updated if the
        address changes, which is why this reads the API's view instead.
        """
        ip = str(row.get("public_ipaddr") or "").strip().rstrip("/")
        ports = row.get("ports") or {}
        if not ip or not isinstance(ports, dict):
            return None
        for internal, bindings in ports.items():
            if str(internal).split("/")[0] != str(self._comfy_port):
                continue
            for binding in bindings or []:
                host_port = str((binding or {}).get("HostPort") or "").strip()
                if host_port:
                    return f"http://{ip}:{host_port}"
        return None

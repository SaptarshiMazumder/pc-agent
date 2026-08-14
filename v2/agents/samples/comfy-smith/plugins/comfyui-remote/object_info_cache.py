"""ObjectInfoCache — the server's node catalogue, fetched once per process.

`/object_info` is the most valuable document in ComfyUI and one of the largest: every installed
node class, its exact inputs, their types, and — critically — the ENUMERATED options for each
model dropdown. It is the difference between "use a KSampler with the dpmpp_2m sampler" (a guess
that fails on a server without that scheduler) and a graph built only from what is installed.

It is also several megabytes, and three of this agent's tools want it in the same turn. Fetching
it per call turns a two-second design step into thirty. So: one fetch per server per process,
with an explicit `refresh` for the case that actually invalidates it — the user just installed a
custom node.

NOT A FALLBACK. If the fetch fails, this raises. A cached-or-empty catalogue would let the
validator report "all nodes exist" about a server it never reached, which is worse than no
validation at all.
"""

from __future__ import annotations

from comfy_client import ComfyClient


class ObjectInfoCache:
    _by_server: dict[str, dict] = {}

    @classmethod
    async def all(cls, client: ComfyClient, refresh: bool = False) -> dict:
        """Every node class installed on this server: {class_name: {input, output, category, …}}."""
        if refresh:
            cls._by_server.pop(client.base, None)
        cached = cls._by_server.get(client.base)
        if cached is not None:
            return cached
        info = await client.get_json("/object_info")
        if not isinstance(info, dict) or not info:
            # An empty catalogue means the endpoint answered with something unexpected. Say so
            # rather than caching nothing and reporting "0 nodes installed" forever after.
            raise RuntimeError(f"/object_info from {client.base} was empty or not an object")
        cls._by_server[client.base] = info
        return info

    @classmethod
    async def node(cls, client: ComfyClient, class_name: str) -> dict | None:
        """One node class. Uses the per-class endpoint when the catalogue is not already loaded —
        a single node is a small request and there is no reason to pull megabytes for it."""
        cached = cls._by_server.get(client.base)
        if cached is not None:
            return cached.get(class_name)
        info = await client.get_json(f"/object_info/{class_name}")
        return (info or {}).get(class_name)

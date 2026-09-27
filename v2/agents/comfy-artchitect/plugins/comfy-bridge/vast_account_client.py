"""The person's own Vast account, read with THEIR API key: which machines they have, and how to
reach one — so connecting a Vast machine is "paste your key, pick your machine", never a hunt
for a token.

The key is a NAME here (`${USER_VAST_API_KEY}`): the host substitutes it into the request header,
so this code never holds it. What comes back per machine: its public IP, the external port Vast
published for the Instance Portal (internal 1111 — the same way the platform finds its own
rentals' portals, vast_marketplace._url_for), and the tokens that may open it. Vast generates the
Open button's token (OPEN_BUTTON_TOKEN); the API reports `jupyter_token`, and `extra_env` when
the person set one. Which of them the portal accepts is settled by asking it
(ComfyConnectionProbe), never assumed.
"""

from __future__ import annotations

_API = "https://console.vast.ai/api/v0"
_AUTH = {"Authorization": "Bearer ${USER_VAST_API_KEY}", "Accept": "application/json"}
_PORTAL_PORT = "1111"


class VastAccountClient:
    def __init__(self, fetch) -> None:
        self._fetch = fetch

    def machines(self) -> list[dict]:
        """Every machine on the account: {id, name, gpu, status, running}."""
        rows = self._get("/instances/").get("instances") or []
        return [self._summary(r) for r in rows if isinstance(r, dict)]

    def access(self, machine_id: int) -> dict:
        """{portal_origins, tokens} for one machine — raises ValueError when it cannot be reached."""
        payload = self._get(f"/instances/{int(machine_id)}/")
        row = payload.get("instances") or payload.get("instance")
        if isinstance(row, list):
            row = row[0] if row else None
        if not isinstance(row, dict):
            raise ValueError(f"Vast has no machine {machine_id} on this account")
        if str(row.get("actual_status") or "") != "running":
            raise ValueError("that Vast machine is not running — start it on Vast, then pick it again")
        ip = str(row.get("public_ipaddr") or "").strip()
        port = self._published(row, _PORTAL_PORT)
        if not ip or not port:
            raise ValueError(
                "that Vast machine publishes no Instance Portal — rent it with Vast's ComfyUI template"
            )
        tokens = [t for t in (self._env(row, "OPEN_BUTTON_TOKEN"), str(row.get("jupyter_token") or "")) if t]
        if not tokens:
            raise ValueError("Vast reported no access token for that machine")
        return {
            "portal_origins": [f"http://{ip}:{port}", f"https://{ip}:{port}"],
            "tokens": list(dict.fromkeys(tokens)),
            "name": self._summary(row)["name"],
        }

    def _get(self, path: str) -> dict:
        res = self._fetch(f"{_API}{path}", headers=_AUTH, timeout_s=20.0)
        if res.status in (401, 403):
            raise ValueError("Vast refused the API key — check it in Workspace → Connection")
        if res.error or not res.ok:
            raise ValueError(f"could not read your Vast account ({res.error or f'HTTP {res.status}'})")
        data = res.json()
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _summary(row: dict) -> dict:
        gpus = int(row.get("num_gpus") or 1)
        gpu = str(row.get("gpu_name") or "GPU")
        status = str(row.get("actual_status") or row.get("cur_state") or "unknown")
        label = str(row.get("label") or "").strip()
        return {
            "id": int(row.get("id") or 0),
            "name": label or f"Vast machine {row.get('id')}",
            "gpu": f"{gpus}× {gpu}" if gpus > 1 else gpu,
            "status": status,
            "running": status == "running",
        }

    @staticmethod
    def _published(row: dict, internal: str) -> str:
        for key, bindings in (row.get("ports") or {}).items():
            if str(key).split("/")[0] != internal:
                continue
            for binding in bindings or []:
                port = str((binding or {}).get("HostPort") or "").strip()
                if port:
                    return port
        return ""

    @staticmethod
    def _env(row: dict, name: str) -> str:
        """One variable from `extra_env`, which Vast reports as [[name, value], …] or a dict."""
        env = row.get("extra_env") or []
        if isinstance(env, dict):
            return str(env.get(name) or "")
        for pair in env:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2 and pair[0] == name:
                return str(pair[1] or "")
        return ""

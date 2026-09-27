"""One of the person's Vast machines, picked from their account, turned into a connection record.

Vast's API gives the machine's portal address and the tokens that may open it
(VastAccountClient); which address scheme and which token the portal actually accepts is found by
asking it — the same check a pasted link gets (ComfyConnectionProbe). Nothing is saved until the
machine has answered.

WHEN NOTHING ANSWERS, EVERY ATTEMPT IS REPORTED. Only the last one used to be: an https try
against a plain-http portal ("wrong version number") hid the http try's real reason — a machine
still starting, or a token the portal refused.
"""

from __future__ import annotations

from urllib.parse import urlencode

from comfy_connection_probe import ComfyConnectionProbe
from vast_account_client import VastAccountClient


class VastMachineConnector:
    def __init__(self, account: VastAccountClient, probe: ComfyConnectionProbe) -> None:
        self._account = account
        self._probe = probe

    def connect(self, machine_id: int) -> dict:
        access = self._account.access(machine_id)
        failures: list[str] = []
        for origin in access["portal_origins"]:
            for n, token in enumerate(access["tokens"], 1):
                try:
                    record = self._probe.probe(f"{origin}/?{urlencode({'token': token})}")
                except ValueError as e:
                    failures.append(f"{origin} (token {n}): {e}")
                    continue
                if record["kind"] != "user_vast":
                    failures.append(f"{origin} (token {n}): answered as ComfyUI, not as Vast's portal")
                    continue
                return {**record, "label": access["name"], "link": "", "vast_machine_id": int(machine_id)}
        raise ValueError(
            "could not open that Vast machine's portal. A machine that has only just started takes a "
            "few minutes to answer — try again shortly. What each attempt said: " + " | ".join(failures)
        )

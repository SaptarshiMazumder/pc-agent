"""Where THIS ACCOUNT's ComfyUI runs — chosen once by the person, used by every chat.

Nothing is rented until the person says so. With no record here the account has not chosen:
gpu_ensure rents nothing and the window asks. The record is one of:

  {kind: rented}                                    the platform's GPU — renting is approved
  {kind: user_vast, url, auth, portal_url, label}   their own Vast machine
  {kind: user_url,  url, auth, label}               any ComfyUI address

`url` is ComfyUI's base (a `?token=` folded as `#q=`, as comfy_bridge._split_query reads it);
`auth` an Authorization header value or ""; `portal_url` the Vast Instance Portal; `label` the
address with no credential in it, for showing. The workspace is the account's own (per account
per agent on the web), so one file is one account's choice.
"""

from __future__ import annotations

import json
from pathlib import Path

RENTED = "rented"
USER_VAST = "user_vast"
USER_URL = "user_url"
KINDS = (RENTED, USER_VAST, USER_URL)
OWN = (USER_VAST, USER_URL)

_FILE = ".studio/comfy-connection.json"


class AccountConnectionRepository:
    def __init__(self, root: Path) -> None:
        self._path = root / _FILE

    def read(self) -> dict | None:
        """The account's choice, or None when it has not made one."""
        if not self._path.is_file():
            return None
        record = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("kind") not in KINDS:
            raise ValueError(f"{self._path.name}: not a connection record")
        if record["kind"] in OWN and not record.get("url"):
            raise ValueError(f"{self._path.name}: an own-ComfyUI record without its address")
        return record

    def save(self, record: dict) -> None:
        if record.get("kind") not in KINDS or (record["kind"] in OWN and not record.get("url")):
            raise ValueError("a connection record needs a kind, and an address for the person's own ComfyUI")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(record), encoding="utf-8")

"""EncryptedFileCredentialStore — the login vault, Fernet-encrypted at ``<state_dir>/credentials.vault``.

The master key comes from the AGENTD_VAULT_KEY env var (a Fernet key) — never the repo, never a
config file. The whole vault (a ``{agent: {site: {...}}}`` map) is encrypted as one blob. Passwords
are decrypted only in-process when the login tool fills a form; they're never returned to the model,
logged, or written in plaintext. A wrong/missing key yields an empty vault rather than a crash.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet

from agent_runtime.domain.credential import CREDENTIAL_FIELDS, Credential


class EncryptedFileCredentialStore:
    def __init__(self, path, key: bytes):
        self._path = Path(path)
        self._fernet = Fernet(key)  # raises on a malformed key (caught by the builder)
        self._data: dict = self._load()

    def _load(self) -> dict:
        if not self._path.is_file():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self._path.read_bytes()))
        except Exception:  # noqa: BLE001 — wrong key / corrupt file -> start empty, never crash
            return {}

    def _save(self) -> None:
        blob = self._fernet.encrypt(json.dumps(self._data).encode("utf-8"))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(blob)
        try:
            os.chmod(self._path, 0o600)  # owner-only (best-effort; no-op on some FS)
        except OSError:
            pass

    def get(self, agent_id: str, site: str) -> Credential | None:
        rec = (self._data.get(agent_id) or {}).get(site)
        if not rec:
            return None
        return Credential(site=site, **{f: rec.get(f, "") for f in CREDENTIAL_FIELDS})

    def put(self, agent_id: str, cred: Credential) -> None:
        self._data.setdefault(agent_id, {})[cred.site] = {
            f: getattr(cred, f) for f in CREDENTIAL_FIELDS
        }
        self._save()

    def delete(self, agent_id: str, site: str) -> bool:
        ok = (self._data.get(agent_id) or {}).pop(site, None) is not None
        if ok:
            self._save()
        return ok

    def list(self, agent_id: str) -> list[str]:
        return sorted((self._data.get(agent_id) or {}).keys())

    def purge_agent(self, agent_id: str) -> int:
        n = len(self._data.pop(agent_id, {}))
        if n:
            self._save()
        return n


def build_credential_store(config):
    """The CredentialStore for the composition root — None unless AGENTD_VAULT_KEY is set, so the
    whole login-vault layer is cut-out-able by a single env var. Generate a key with
    ``python -m agent_runtime.main.add_login --genkey``."""
    key = os.environ.get("AGENTD_VAULT_KEY", "").strip()
    if not key:
        return None
    try:
        path = Path(getattr(config, "state_dir", ".")) / "credentials.vault"
        return EncryptedFileCredentialStore(path, key.encode("utf-8"))
    except Exception:  # noqa: BLE001 — a malformed key disables the vault rather than crashing boot
        return None

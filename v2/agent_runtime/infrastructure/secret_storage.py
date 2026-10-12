"""Atomic encrypted secret records; the key lives outside tenant files."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path


def _cipher():
    value = os.environ.get("AGENTD_STATE_KEK", "")
    if not value:
        if os.environ.get("AGENTD_REQUIRE_STATE_ENCRYPTION") == "1":
            raise RuntimeError("state encryption key is required")
        return None
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(("agentd-state-v1:" + value).encode()).digest())
    return Fernet(key)


def read(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "ciphertext" in data:
        cipher = _cipher()
        if cipher is None:
            raise ValueError("state encryption key is unavailable")
        from cryptography.fernet import InvalidToken

        try:
            data = json.loads(cipher.decrypt(str(data["ciphertext"]).encode()))
        except InvalidToken as exc:
            raise ValueError("secret record failed authentication") from exc
    return data


def write(path: Path, data: dict) -> None:
    cipher = _cipher()
    if cipher is not None:
        data = {"version": 1, "ciphertext": cipher.encrypt(json.dumps(data).encode()).decode()}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".secret-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

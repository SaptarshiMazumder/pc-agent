"""Encrypted credential vault: per-(agent,site) login roundtrip, agent isolation, purge/delete;
wrong/missing key -> empty/disabled; secrets are NOT plaintext on disk."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import Fernet

from agent_runtime.domain.credential import Credential
from agent_runtime.infrastructure.credentials import (
    EncryptedFileCredentialStore,
    build_credential_store,
)

KEY = Fernet.generate_key()


def _cred(site="hp"):
    return Credential(
        site=site,
        login_url="https://hp/login",
        username="u@e.com",
        password="SECRET-PW",
        otp_selector="#otp",
    )


def test_put_get_roundtrip(tmp_path):
    s = EncryptedFileCredentialStore(tmp_path / "v.vault", KEY)
    s.put("main", _cred())
    c = s.get("main", "hp")
    assert c is not None and c.username == "u@e.com" and c.password == "SECRET-PW"
    assert c.otp_selector == "#otp" and c.login_url == "https://hp/login"
    assert s.get("main", "nope") is None


def test_persists_encrypted_not_plaintext(tmp_path):
    p = tmp_path / "v.vault"
    EncryptedFileCredentialStore(p, KEY).put("main", _cred())
    blob = p.read_bytes()
    assert b"SECRET-PW" not in blob and b"u@e.com" not in blob  # encrypted, not plaintext
    # a fresh store with the SAME key reads it back
    assert EncryptedFileCredentialStore(p, KEY).get("main", "hp").password == "SECRET-PW"


def test_wrong_key_yields_empty(tmp_path):
    p = tmp_path / "v.vault"
    EncryptedFileCredentialStore(p, KEY).put("main", _cred())
    other = EncryptedFileCredentialStore(p, Fernet.generate_key())  # different key -> can't read
    assert other.get("main", "hp") is None and other.list("main") == []


def test_isolation_and_purge(tmp_path):
    s = EncryptedFileCredentialStore(tmp_path / "v.vault", KEY)
    s.put("a", _cred("x"))
    s.put("b", _cred("y"))
    assert s.list("a") == ["x"] and s.list("b") == ["y"]
    assert s.get("a", "y") is None  # b's site invisible to a
    assert s.purge_agent("a") == 1 and s.list("a") == []
    assert s.list("b") == ["y"]  # b untouched
    assert s.delete("b", "y") is True and s.get("b", "y") is None


def test_build_gated_by_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTD_VAULT_KEY", raising=False)
    assert build_credential_store(SimpleNamespace(state_dir=tmp_path)) is None
    monkeypatch.setenv("AGENTD_VAULT_KEY", KEY.decode())
    assert build_credential_store(SimpleNamespace(state_dir=tmp_path)) is not None

"""Persistent ownership, encrypted signing and R2 upload consumption contracts."""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from agent_runtime.infrastructure import signing
from agent_runtime.infrastructure.publish.r2_upload_slots import R2UploadSlots
from agent_runtime.infrastructure.publish.sqlite_registry import FileIndexLock, adapters


@pytest.mark.parametrize("method", ["POST", "PUT"])
def test_publisher_storage_transfer_never_receives_platform_token(tmp_path, monkeypatch, method):
    import httpx

    from agent_runtime.infrastructure.marketplace.http_publisher import HttpRegistryPublisher

    monkeypatch.setenv("AGENTD_SESSION_TOKEN", "platform-session")
    requests = []

    def response(status, body):
        return SimpleNamespace(status_code=status, text=json.dumps(body), json=lambda: body)

    def post(url, **kwargs):
        requests.append(("POST", url, kwargs))
        if url.endswith("/upload"):
            return response(
                200,
                {"upload": {"url": "https://objects.test/upload", "key": "slot", "method": method}},
            )
        if url == "https://objects.test/upload":
            return response(204, {})
        return response(200, {"url": "https://registry.test/bundle"})

    def put(url, **kwargs):
        requests.append(("PUT", url, kwargs))
        return response(200, {})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "put", put)
    package = tmp_path / "test.agentpkg"
    package.write_bytes(b"bytes")
    publisher = HttpRegistryPublisher("https://publish.test", None, None)
    result = publisher._send(SimpleNamespace(id="test", version="1.0"), package)
    assert result.ok
    storage = [request for request in requests if request[1] == "https://objects.test/upload"][0]
    assert storage[0] == method
    assert "Authorization" not in storage[2].get("headers", {})
    assert "platform-session" not in str(storage)
    assert requests[-1][2]["json"]["upload_key"] == "slot"


@pytest.fixture
def registry(tmp_path):
    return adapters(tmp_path / "registry.db", Fernet.generate_key().decode())


def test_creator_registration_and_bundle_claim_are_atomic(registry):
    _, creators, signer, _, _ = registry
    with ThreadPoolExecutor(max_workers=8) as pool:
        registered = list(
            pool.map(lambda _: creators.for_account("same-account", "Author"), range(8))
        )
    assert len({c.id for c in registered}) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        owners = list(pool.map(lambda n: creators.claim("bundle", f"creator-{n}"), range(8)))
    assert len(set(owners)) == 1
    creator = registered[0]
    signature = signer.sign(creator.id, b"digest")
    assert signing.verify(creator.public_key, b"digest", signature)
    assert creators.private_key(creator.id).startswith("gAAAA")
    assert "private_key" not in creators.all()[0]
    creators.admit(creator.id)
    assert creators.for_account("same-account").state == "listed"
    creators.revoke(creator.id)
    assert creators.for_account("same-account").state == "revoked"


def test_root_import_preserves_trust_and_refuses_rotation(registry):
    _, creators, _, vault, _ = registry
    with pytest.raises(RuntimeError, match="import"):
        vault.private_key()
    private, public = signing.generate_keypair()
    vault.store(private, public)
    assert vault.private_key() == private and vault.public_key() == public
    assert creators.all() == []
    with pytest.raises(ValueError, match="rotation"):
        vault.store(*signing.generate_keypair())
    with pytest.raises(ValueError, match="mismatch"):
        vault.store(signing.generate_keypair()[0], public)


def test_kernel_index_lock_blocks_second_writer_and_releases_on_error(tmp_path):
    first = FileIndexLock(tmp_path / "index.lock", timeout=0.1)
    second = FileIndexLock(tmp_path / "index.lock", timeout=0.1)
    with pytest.raises(ValueError), first:
        with pytest.raises(TimeoutError), second:
            pass
        raise ValueError("failed publish")
    with second:
        pass


class S3:
    def __init__(self):
        self.objects = {}
        self.reads = 0

    def generate_presigned_url(self, *args, **kwargs):
        return "https://objects.test/upload?signature=storage-only"

    def head_object(self, Bucket, Key):
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, Bucket, Key):
        self.reads += 1
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


def test_private_r2_slots_enforce_owner_expiry_size_and_one_time_claim(registry):
    database = registry[0]
    s3 = S3()
    slots = R2UploadSlots(s3, "private", database)
    slot = slots.reserve("owner", 5)
    assert slot.method == "PUT" and slot.headers == {"Content-Type": "application/octet-stream"}
    s3.objects[slot.key] = b"okay"
    assert slots.take("other-owner", slot.key) == b""
    assert slots.take("owner", slot.key) == b"okay"
    s3.objects[slot.key] = b"reuse"
    assert slots.take("owner", slot.key) == b""
    large = slots.reserve("owner", 5)
    s3.objects[large.key] = b"too large"
    reads = s3.reads
    assert slots.take("owner", large.key) == b""
    assert s3.reads == reads and large.key not in s3.objects
    expired = slots.reserve("owner", 5)
    with database.connect() as db:
        db.execute("UPDATE uploads SET expires=0 WHERE key=?", (expired.key,))
    assert slots.take("owner", expired.key) == b""

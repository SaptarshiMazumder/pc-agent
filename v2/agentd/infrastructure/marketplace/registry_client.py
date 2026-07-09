"""Registry client — ONE implementation for all three registry shapes:

  * a local directory containing index.json (+ .agentpkg files)   — dev/local
  * a file:// URL to index.json (or a directory)                  — dev/local
  * an http(s) URL to index.json (or a directory)                 — the real CDN

Relative bundle `url`s resolve against the index location, so the SAME folder works
served from disk today and uploaded to a CDN tomorrow ("local now, cloud later").

Verification (fail closed): sha256 always when the index carries one; ed25519
signature when a publisher key is PINNED (distribution profile wins over the
index's self-declared key — an installer-baked key can't be spoofed by a registry)."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import url2pathname

import httpx

from agentd.domain.bundle import BundleError, RegistryEntry, RegistryIndex, parse_registry_index
from agentd.infrastructure import signing
from agentd.infrastructure.marketplace.bundle_io import sha256_file

log = logging.getLogger("agentd")


def normalize_registry_url(raw: str) -> str:
    """dir path -> file:// index URL; bare index path -> file:// URL; URLs pass through
    (a URL not ending in .json is treated as a directory)."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://", "file://")):
        return raw if raw.endswith(".json") else raw.rstrip("/") + "/index.json"
    path = Path(raw).expanduser()
    if path.is_dir() or not raw.endswith(".json"):
        path = path / "index.json"
    return path.resolve().as_uri()


class RegistryClient:
    """The one concrete RegistryClient (see application/interfaces/marketplace)."""

    def __init__(self, registry_url: str, pinned_publisher_key: str = ""):
        self._index_url = normalize_registry_url(registry_url)
        self._pinned_key = pinned_publisher_key

    async def fetch_index(self) -> RegistryIndex:
        raw = await self._read_bytes(self._index_url)
        try:
            index = parse_registry_index(json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as e:
            raise BundleError(f"registry index at {self._index_url} is not valid: {e}") from e
        return index

    async def download(self, entry: RegistryEntry, dest_dir: Path) -> Path:
        if not entry.url:
            raise BundleError(f"registry entry '{entry.id}' has no artifact url")
        artifact_url = urljoin(self._index_url, entry.url)
        dest = dest_dir / f"{entry.id}-{entry.version}.agentpkg"
        data = await self._read_bytes(artifact_url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self._verify(entry, dest)
        return dest

    # ------------------------------------------------------------ verification

    def _verify(self, entry: RegistryEntry, artifact: Path) -> None:
        digest = sha256_file(artifact)
        if entry.sha256 and digest != entry.sha256.lower():
            artifact.unlink(missing_ok=True)
            raise BundleError(
                f"'{entry.id}': sha256 mismatch — refusing corrupted/tampered "
                f"artifact (got {digest[:12]}…, want {entry.sha256[:12]}…)"
            )
        if self._pinned_key:  # a pinned key makes signatures MANDATORY (fail closed)
            if not entry.sig:
                artifact.unlink(missing_ok=True)
                raise BundleError(
                    f"'{entry.id}': unsigned artifact but this install pins a "
                    f"publisher key — refusing"
                )
            if not signing.verify(self._pinned_key, digest.encode("ascii"), entry.sig):
                artifact.unlink(missing_ok=True)
                raise BundleError(f"'{entry.id}': publisher signature INVALID — refusing")
            log.info("bundle %s: signature verified against the pinned publisher key", entry.id)

    # ------------------------------------------------------------ transports

    async def _read_bytes(self, url: str) -> bytes:
        scheme = urlsplit(url).scheme
        if scheme == "file":
            path = Path(url2pathname(urlsplit(url).path))
            try:
                return await asyncio.to_thread(path.read_bytes)
            except OSError as e:
                raise BundleError(f"cannot read {path}: {e}") from e
        if scheme in ("http", "https"):
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.content
            except httpx.HTTPError as e:
                raise BundleError(f"registry fetch failed for {url}: {e}") from e
        raise BundleError(f"unsupported registry URL scheme: {url}")

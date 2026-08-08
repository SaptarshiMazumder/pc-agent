"""Registry client — ONE implementation for all three registry shapes:

  * a local directory containing index.json (+ .agentpkg files)   — dev/local
  * a file:// URL to index.json (or a directory)                  — dev/local
  * an http(s) URL to index.json (or a directory)                 — the real CDN

Relative bundle `url`s resolve against the index location, so the SAME folder works
served from disk today and uploaded to a CDN tomorrow ("local now, cloud later").

Verification (fail closed): sha256 always when the index carries one; ed25519
signature when a publisher key is PINNED (distribution profile wins over the
index's self-declared key — an installer-baked key can't be spoofed by a registry).

TWO TRUST SHAPES, one pinned key either way:

  * schema 1 — one publisher. The pinned key signed every bundle directly.
  * schema 2 — many creators. The pinned key is the PLATFORM ROOT key; it signed a ROSTER of
    creators, and each creator's own key signed their own bundles. Trust is therefore derived:
    verify the roster with the pinned key, then verify each bundle against ITS creator's key.

The pinned key is the same field in both (`distribution.publisher_key`), which is the entire point
— adding a creator must never require re-pinning an already-installed client."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import url2pathname

import httpx

from agent_runtime.domain.bundle import (
    BundleError,
    RegistryEntry,
    RegistryIndex,
    parse_registry_index,
)
from agent_runtime.infrastructure import signing
from agent_runtime.infrastructure.marketplace.bundle_io import sha256_file
from agent_runtime.infrastructure.marketplace.trust import RosterMemory, verify_roster

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

    def __init__(
        self,
        registry_url: str,
        pinned_publisher_key: str = "",
        trust_state_path: Path | None = None,
    ):
        self._index_url = normalize_registry_url(registry_url)
        self._pinned_key = pinned_publisher_key
        self._memory = RosterMemory(trust_state_path)
        # Filled by fetch_index for a schema-2 registry: creator id -> public key. Empty means
        # either schema 1 or "no index has been fetched yet", and `_verify` distinguishes the two
        # by whether the entry names a publisher_id.
        self._trusted: dict[str, str] = {}

    async def fetch_index(self) -> RegistryIndex:
        raw = await self._read_bytes(self._index_url)
        try:
            index = parse_registry_index(json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as e:
            raise BundleError(f"registry index at {self._index_url} is not valid: {e}") from e
        self._establish_trust(index)
        return index

    def _establish_trust(self, index: RegistryIndex) -> None:
        """Schema 2: verify the roster with the pinned root key and keep the creator key map.

        This runs on the INDEX fetch rather than on download, because a bad roster invalidates the
        whole listing, not one bundle — and a store that renders cards from a listing it has
        already decided it cannot trust is a store that will happily offer you the install.
        """
        self._trusted = {}
        if index.schema < 2:
            return
        roster = index.publishers
        if roster is None:
            raise BundleError("registry index: schema 2 with no `publishers` roster — refusing")
        if not verify_roster(roster, self._pinned_key):
            raise BundleError(
                "registry index: the publisher roster's signature does not match this install's "
                "pinned platform key — refusing the whole registry"
            )
        if self._memory.is_downgrade(self._index_url, roster.issued):
            raise BundleError(
                f"registry index: this roster ({roster.issued}) is OLDER than one already accepted "
                f"from {self._index_url} ({self._memory.newest_seen(self._index_url)}). That is what "
                "a replayed index looks like — refusing. If the rollback is intentional, clear the "
                "registry trust file."
            )
        self._trusted = roster.trusted_keys()
        if self._pinned_key:
            self._memory.remember(self._index_url, roster.issued)
        log.info(
            "registry: roster verified — %d trusted creator(s), %d revoked",
            len(self._trusted),
            len(roster.revoked),
        )

    def asset_url(self, relative: str) -> str:
        """Absolute url for a registry-relative artifact path.

        Index urls are relative so the same folder works from disk and from a CDN; joining them is
        this client's job because it is what knows where the index came from. Exposed for assets a
        client links to rather than downloads through us — installers, which a browser fetches
        directly. An already-absolute url passes through (urljoin's own rule)."""
        return urljoin(self._index_url, relative) if relative else ""

    async def download(self, entry: RegistryEntry, dest_dir: Path) -> Path:
        if not entry.url:
            raise BundleError(f"registry entry '{entry.id}' has no artifact url")
        artifact_url = self.asset_url(entry.url)
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
        if not self._pinned_key:  # unpinned install: sha256 is the whole check (dev / local)
            return
        # A pinned key makes signatures MANDATORY (fail closed).
        if not entry.sig:
            artifact.unlink(missing_ok=True)
            raise BundleError(
                f"'{entry.id}': unsigned artifact but this install pins a publisher key — refusing"
            )
        key, whose = self._signing_key(entry, artifact)
        if not signing.verify(key, digest.encode("ascii"), entry.sig):
            artifact.unlink(missing_ok=True)
            raise BundleError(f"'{entry.id}': {whose} signature INVALID — refusing")
        log.info("bundle %s: signature verified against %s", entry.id, whose)

    def _signing_key(self, entry: RegistryEntry, artifact: Path) -> tuple[str, str]:
        """WHICH key should have signed this bundle — the pinned one, or its creator's."""
        if not entry.publisher_id:
            return self._pinned_key, "the pinned publisher key"
        key = self._trusted.get(entry.publisher_id)
        if not key:
            artifact.unlink(missing_ok=True)
            # Covers three cases that all mean the same thing to a user — the creator is unknown,
            # they were revoked, or the roster was never established because the entry arrived from
            # somewhere other than a verified index. All three are "we cannot vouch for this".
            raise BundleError(
                f"'{entry.id}': published by '{entry.publisher_id}', who is not a trusted creator "
                "on this registry's roster (unknown, revoked, or the roster was not verified) — "
                "refusing"
            )
        return key, f"creator '{entry.publisher_id}'"

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

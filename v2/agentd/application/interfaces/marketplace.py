"""Marketplace contracts (M4) — what the install use-case needs, not how it's done.

The MarketplaceService orchestrates: resolve entry -> download -> verify -> unpack ->
pip deps -> record -> hot-reload hooks. Everything concrete (httpx, zipfile, pip,
the JSON ledger) hides behind these Protocols, injected at the composition root —
so the service is testable with fakes and the transports are swappable."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from agentd.domain.bundle import BundleManifest, InstalledBundle, RegistryEntry, RegistryIndex


@runtime_checkable
class RegistryClient(Protocol):
    """Fetch the index + download artifacts. Implementations: local dir, file://,
    http(s) — one client handles all three (infrastructure/marketplace)."""

    async def fetch_index(self) -> RegistryIndex: ...

    async def download(self, entry: RegistryEntry, dest_dir: Path) -> Path:
        """Fetch the entry's artifact into dest_dir, VERIFYING sha256 (and signature
        when the index/profile carries a publisher key). Returns the local path."""
        ...


@runtime_checkable
class BundleInstaller(Protocol):
    """Materialize / remove a bundle's files on disk (zip-safe unpack, pip deps)."""

    def read_manifest(self, package_path: Path) -> BundleManifest: ...

    async def install_files(self, package_path: Path, manifest: BundleManifest) -> list[str]:
        """Unpack agent/ + vendored plugins/, install pip deps. Returns the vendored
        plugin ids actually placed (for the ledger / uninstall refcounting)."""
        ...

    async def remove_files(self, installed: InstalledBundle, keep_plugin_ids: set[str],
                           purge_state: bool = False) -> None: ...


@runtime_checkable
class InstalledStore(Protocol):
    """The durable ledger of what this install has installed (drives update checks,
    uninstall, store badges)."""

    def list(self) -> list[InstalledBundle]: ...

    def get(self, bundle_id: str) -> InstalledBundle | None: ...

    def record(self, bundle: InstalledBundle) -> None: ...

    def remove(self, bundle_id: str) -> None: ...

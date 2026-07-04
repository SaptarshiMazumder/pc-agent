"""Marketplace composition — the ONE place the concrete pieces are named.

Used by BOTH composition roots: the gateway (live daemon: hot-reload + broadcast
progress) and the CLI (offline installs: effects apply next daemon start)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentd import __version__
from agentd.application.services.marketplace_service import MarketplaceService
from agentd.infrastructure.marketplace.installed_store import JsonInstalledStore
from agentd.infrastructure.marketplace.installer import FileBundleInstaller
from agentd.infrastructure.marketplace.registry_client import RegistryClient


def build_marketplace_service(
    config,
    on_event: Callable[[dict], None] | None = None,
    after_change: Callable[[], dict] | None = None,
    registry_url: str = "",
) -> MarketplaceService:
    url = registry_url or getattr(config, "registry_url", "")
    profile = getattr(config, "distribution", None)
    pinned_key = getattr(profile, "publisher_key", "") if profile else ""
    state_dir = Path(config.state_dir)
    return MarketplaceService(
        registry_client=RegistryClient(url, pinned_publisher_key=pinned_key) if url else None,
        installer=FileBundleInstaller(
            agents_dir=Path(config.agents_dir),
            plugins_dir=Path(config.plugins_dir),
            state_dir=state_dir,
            builtin_plugins_dir=Path(config.builtin_plugins_dir)
            if getattr(config, "builtin_plugins_dir", "") else None,
        ),
        installed_store=JsonInstalledStore(state_dir / "installed_bundles.json"),
        agentd_version=__version__,
        download_dir=state_dir / "downloads",
        on_event=on_event,
        after_change=after_change,
    )

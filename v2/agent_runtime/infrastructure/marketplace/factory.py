"""Marketplace composition — the ONE place the concrete pieces are named.

Used by BOTH composition roots: the gateway (live daemon: hot-reload + broadcast
progress) and the CLI (offline installs: effects apply next daemon start)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_runtime import __version__
from agent_runtime.application.services.marketplace_service import MarketplaceService
from agent_runtime.infrastructure.marketplace.installed_store import JsonInstalledStore
from agent_runtime.infrastructure.marketplace.federated_client import (
    FederatedRegistryClient,
    org_index_url,
)
from agent_runtime.infrastructure.marketplace.installer import FileBundleInstaller
from agent_runtime.infrastructure.marketplace.registry_client import RegistryClient


def build_marketplace_service(
    config,
    on_event: Callable[[dict], None] | None = None,
    after_change: Callable[[], dict] | None = None,
    registry_url: str = "",
    org_ids: tuple[str, ...] = (),
) -> MarketplaceService:
    """:param org_ids: the organizations THIS CALLER belongs to, from their verified token. Each
    one adds that organization's private registry to the listing beside the public marketplace,
    so an agent a colleague published internally installs exactly like a marketplace one. Empty
    (the CLI, a signed-out desktop) => the public registry alone, byte-for-byte as before."""
    url = registry_url or getattr(config, "registry_url", "")
    profile = getattr(config, "distribution", None)
    pinned_key = getattr(profile, "publisher_key", "") if profile else ""
    state_dir = Path(config.state_dir)

    def client(index_url: str, auth_token=None, normalize: bool = True) -> RegistryClient:
        return RegistryClient(
            index_url,
            pinned_publisher_key=pinned_key,
            # Where the newest roster date accepted from each registry is remembered, which is
            # what makes a replayed index detectable. Per state_dir, so a hosted account's memory
            # is its own: the marketplace service is already built per account.
            trust_state_path=state_dir / "registry_trust.json",
            auth_token=auth_token,
            normalize=normalize,
        )

    public = client(url) if url else None
    # THE SAME PINNED ROOT KEY on every shelf. An org registry is a prefix inside this registry,
    # not a second trust domain, so its roster is root-signed and verified exactly like the
    # public one -- see FederatedRegistryClient.
    #
    # READ THROUGH THE PUBLISH SERVICE, not the CDN: `orgs/*` is not publicly readable, so the
    # index arrives from an authenticated endpoint that checks membership and presigns the
    # artifact links. Hence the publish url here rather than the registry url, and hence a token.
    # profile first (a desktop installer bakes it), then the resolved config value, which is
    # where a HOSTED daemon's AGENTD_PUBLISH_URL lands. Either way one answer, so a member
    # sees the same org shelf on the web app and in the desktop app.
    profile_publish_url = (getattr(profile, "publish_url", "") if profile else "") or str(
        getattr(config, "publish_target", "") or ""
    )

    def session_token() -> str:
        # Resolved PER REQUEST, never captured: a session is refreshed while the daemon runs, and
        # a token frozen at construction would 401 for the rest of the process's life.
        from agent_runtime.infrastructure.marketplace.http_publisher import platform_session_token

        return platform_session_token(config)

    shelves = [
        # normalize=False: org_index_url already names the endpoint. Appending "index.json"
        # would make the org id read as that filename and the service would refuse it.
        (
            org,
            client(
                org_index_url(profile_publish_url, org),
                auth_token=session_token,
                normalize=False,
            ),
        )
        for org in org_ids
        if org_index_url(profile_publish_url, org)
    ]

    return MarketplaceService(
        registry_client=FederatedRegistryClient(public, shelves) if shelves else public,
        installer=FileBundleInstaller(
            agents_dir=Path(config.agents_dir),
            plugins_dir=Path(config.plugins_dir),
            state_dir=state_dir,
            builtin_plugins_dir=Path(config.builtin_plugins_dir)
            if getattr(config, "builtin_plugins_dir", "")
            else None,
            hosted=bool(getattr(config, "hosted", False)),
        ),
        installed_store=JsonInstalledStore(state_dir / "installed_bundles.json"),
        agentd_version=__version__,
        download_dir=state_dir / "downloads",
        on_event=on_event,
        after_change=after_change,
    )

"""MarketplaceService (M4) — the install/uninstall/catalog use-case.

Pure orchestration over the injected contracts (RegistryClient / BundleInstaller /
InstalledStore): no IO libraries here. The gateway exposes it as marketplace.* RPCs;
the CLI drives it directly when no daemon is running. Post-install HOT-RELOAD is a
callback the composition root wires (agent registry refresh + live plugin load) —
this service doesn't know what "reload" means, only when it's due.

Progress: every step emits through ``on_event`` (dict payloads) so the desktop store
can render a live progress bar; the CLI just prints them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from agent_runtime.application.interfaces.marketplace import (
    BundleInstaller,
    InstalledStore,
    RegistryClient,
)
from agent_runtime.domain.bundle import (
    BundleError,
    InstalledBundle,
    RegistryEntry,
    compat_ok,
    is_update,
)

log = logging.getLogger("agentd")


class MarketplaceService:
    def __init__(
        self,
        *,
        registry_client: RegistryClient | None,  # None => no registry configured
        installer: BundleInstaller,
        installed_store: InstalledStore,
        agentd_version: str,
        download_dir: Path,
        on_event: Callable[[dict], None] | None = None,  # progress -> UI (gateway broadcast)
        # hot-reload hook (composition root). Receives {"plugins": [ids the change placed]}
        # so a provisioning-gated install (Studio flavor) can extend the provisioned set
        # BEFORE re-discovery — an acquired addon joins the profile (tiers doc §3).
        after_change: Callable[[dict], dict] | None = None,
    ):
        self._registry = registry_client
        self._installer = installer
        self._store = installed_store
        self._version = agentd_version
        self._download_dir = download_dir
        self._on_event = on_event or (lambda payload: None)
        self._after_change = after_change or (lambda changed: {})

    # ------------------------------------------------------------------ queries

    async def catalog(self) -> dict:
        """The store view: registry entries merged with installed state + compat."""
        if self._registry is None:
            return {
                "bundles": [],
                "error": "no registry configured "
                "(set registry_url in config, AGENTD_REGISTRY, or the distribution profile)",
            }
        index = await self._registry.fetch_index()
        installed = {b.id: b for b in self._store.list()}
        resolve = getattr(self._registry, "asset_url", None)
        bundles = []
        for entry in index.bundles:
            have = installed.get(entry.id)
            bundles.append(
                {
                    **_entry_dict(entry, resolve, web_host=index.web_host),
                    "compatible": compat_ok(self._version, entry.agentd_compat),
                    "installed": have is not None,
                    "installedVersion": have.version if have else "",
                    "updateAvailable": bool(have and is_update(have.version, entry.version)),
                }
            )
        return {"registry": index.name, "publisher": index.publisher, "bundles": bundles}

    def installed(self) -> dict:
        return {
            "bundles": [
                {
                    "id": b.id,
                    "version": b.version,
                    "installedAt": b.installed_at,
                    "source": b.source,
                    "pluginIds": list(b.plugin_ids),
                    "entitlement": b.entitlement,
                }
                for b in self._store.list()
            ]
        }

    # ------------------------------------------------------------------ install

    async def install(self, bundle_id: str = "", file: str = "") -> dict:
        """Install by registry id OR from a local .agentpkg file. Steps emit progress;
        any failure raises BundleError with a user-renderable message."""
        if file:
            package_path = Path(file)
            if not package_path.is_file():
                raise BundleError(f"no such bundle file: {file}")
            source = str(package_path)
        elif bundle_id:
            package_path, source = await self._fetch(bundle_id)
        else:
            raise BundleError("install needs a bundle id or a file path")

        manifest = self._installer.read_manifest(package_path)
        self._emit(manifest.id, "resolved", f"{manifest.name} {manifest.version}")
        if not compat_ok(self._version, manifest.agentd_compat):
            raise BundleError(
                f"'{manifest.id}' needs agentd {manifest.agentd_compat} (this is {self._version}) "
                f"— update agentd first"
            )

        self._emit(manifest.id, "installing", "unpacking files")
        plugin_ids = await self._installer.install_files(package_path, manifest)
        self._store.record(
            InstalledBundle(
                id=manifest.id,
                version=manifest.version,
                installed_at=datetime.now().isoformat(timespec="seconds"),
                source=source,
                plugin_ids=tuple(plugin_ids),
                entitlement=manifest.entitlement,
            )
        )
        self._emit(manifest.id, "reloading", "activating agent + plugins")
        reload_result = self._after_change({"plugins": plugin_ids})
        self._emit(manifest.id, "installed", f"{manifest.name} {manifest.version} ready")
        log.info(
            "marketplace: installed %s %s (plugins: %s)",
            manifest.id,
            manifest.version,
            ", ".join(plugin_ids) or "none",
        )
        return {
            "installed": True,
            "id": manifest.id,
            "version": manifest.version,
            "plugins": plugin_ids,
            "reload": reload_result,
        }

    def has(self, bundle_id: str) -> bool:
        """Did THIS service's ledger install the bundle? Distinguishes a marketplace install
        from a curated agent that merely shares the directory."""
        return self._store.get(bundle_id) is not None

    async def sync_web_app(self, bundle_id: str) -> dict:
        """Make the hosted copy of a WEB-DELIVERED bundle current: install when missing, update
        when the registry is newer, no-op when current.

        This is the door a visitor's `GET /apps/<id>` opens, which is why it re-checks the
        author's opt-in instead of trusting the caller: web delivery is what authorizes running
        an agent on the PLATFORM's infrastructure, and a bare `install` here would let any URL
        guess conscript the host into running any published bundle."""
        if self._registry is None:
            raise BundleError("no registry configured")
        index = await self._registry.fetch_index()
        entry = next((e for e in index.bundles if e.id == bundle_id), None)
        if entry is None:
            raise BundleError(f"'{bundle_id}' is not in the registry")
        if not entry.delivery.web:
            raise BundleError(f"'{bundle_id}' does not offer web delivery")
        have = self._store.get(bundle_id)
        if have is not None and not is_update(have.version, entry.version):
            return {"installed": True, "id": bundle_id, "version": have.version, "current": True}
        result = await self.install(bundle_id)
        # HOSTED ≠ ENDORSED. The install above stamped this copy `curated` (a platform install is
        # curation — true for the boot seed, false here): this copy exists because an AUTHOR
        # published web=true, and leaving it curated would put every web publish in every user's
        # sidebar. Re-stamped `web-app`: reachable at its URL, listed in the Store, auto-listed
        # for nobody.
        agents_dir = getattr(self._installer, "agents_dir", None)
        if agents_dir is not None:
            from agent_runtime.domain import ownership
            from agent_runtime.infrastructure.agents import ownership_store

            ownership_store.write(
                Path(agents_dir) / bundle_id,
                ownership.OwnershipRecord(
                    owner=ownership.PLATFORM_OWNER,
                    origin=ownership.WEB_APP,
                    source_id=bundle_id,
                    source_version=str(entry.version),
                ),
            )
            self._after_change({})  # re-scan so the registry sees the new provenance
        return result

    async def uninstall(self, bundle_id: str, purge_state: bool = False) -> dict:
        installed = self._store.get(bundle_id)
        if installed is None:
            raise BundleError(
                f"'{bundle_id}' is not an installed bundle "
                f"(see `agentd bundles` / the store's Installed tab)"
            )
        # a vendored plugin shared with ANOTHER installed bundle must survive
        keep = {pid for b in self._store.list() if b.id != bundle_id for pid in b.plugin_ids}
        await self._installer.remove_files(installed, keep_plugin_ids=keep, purge_state=purge_state)
        self._store.remove(bundle_id)
        reload_result = self._after_change({})
        self._emit(bundle_id, "uninstalled", "removed")
        log.info("marketplace: uninstalled %s", bundle_id)
        return {"uninstalled": True, "id": bundle_id, "reload": reload_result}

    # ------------------------------------------------------------------ internals

    async def _fetch(self, bundle_id: str) -> tuple[Path, str]:
        if self._registry is None:
            raise BundleError(
                "no registry configured — pass a .agentpkg file, or set "
                "registry_url / AGENTD_REGISTRY"
            )
        index = await self._registry.fetch_index()
        entry = next((e for e in index.bundles if e.id == bundle_id), None)
        if entry is None:
            known = ", ".join(e.id for e in index.bundles) or "(empty registry)"
            raise BundleError(f"'{bundle_id}' not in the registry — available: {known}")
        self._emit(bundle_id, "downloading", f"{entry.name} {entry.version}")
        self._download_dir.mkdir(parents=True, exist_ok=True)
        package_path = await self._registry.download(entry, self._download_dir)
        return package_path, entry.url or bundle_id

    def _emit(self, bundle_id: str, step: str, message: str) -> None:
        try:
            self._on_event({"id": bundle_id, "step": step, "message": message})
        except Exception:  # noqa: BLE001 — progress must never break an install
            log.debug("marketplace progress emit failed", exc_info=True)


def _entry_dict(entry: RegistryEntry, resolve=None, web_host: str = "") -> dict:
    """One catalog row. `resolve` turns a registry-relative url into an absolute one.

    Installer urls are resolved HERE rather than in the client because the registry base url is
    the daemon's knowledge (config / distribution profile), and a client that had to join them
    itself would need the base url shipped to it just to build a link — one more thing to keep in
    sync for no gain. What the client receives is a url it can hand straight to the browser.
    `webUrl` follows the same rule: the hosted deployment's address is registry knowledge
    (index-level, like `engine`), so the client gets a finished link or nothing.
    """
    out = {
        "id": entry.id,
        "name": entry.name,
        "version": entry.version,
        "description": entry.description,
        "agentdCompat": entry.agentd_compat,
        "price": entry.price,
        "entitlement": entry.entitlement,
        "size": entry.size,
        "icon": entry.icon,
        "delivery": {"web": entry.delivery.web, "exe": entry.delivery.exe},
    }
    if entry.delivery.web and web_host:
        out["webUrl"] = f"{web_host.rstrip('/')}/apps/{entry.id}/"
    if entry.installers:
        out["installers"] = [
            {
                "platform": a.platform,
                "url": resolve(a.url) if resolve else a.url,
                "size": a.size,
                "sha256": a.sha256,
            }
            for a in entry.installers
        ]
    return out

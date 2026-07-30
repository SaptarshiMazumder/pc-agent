"""Distribution profile — WHAT THIS INSTALL IS (product name, provisioned plugins,
default agent, store/registry wiring). One optional ``distribution.toml`` baked into
an installer (or dropped into ~/.agentd) turns the same codebase into a different
product: the core app, "Figure Creator Studio", etc. No file => the OPEN profile —
everything provisioned, store enabled, product "agentd".

This implements the **Provisioned** gate from
planning/platform/tools/plugin-distribution-architecture.md (§2): *Installed* is
plugin discovery, *Enabled* is the config toggles, and Provisioned — "is this plugin
part of THIS install's tier?" — is `provisioned_plugins` here, applied at the same
discovery chokepoint. `None` means "everything" (the open/default behavior), so a
checkout or a plain `pip install agentd` never loses tools.

File shape (all keys optional):

    [product]
    id = "figure-creator-studio"
    name = "Figure Creator Studio"
    default_agent = "figure-creator"
    preinstalled_bundles = ["figure-creator"]

    [provisioning]
    plugins = ["core_fs", "shell", "web", "figures", ...]   # omit => all

    [store]
    enabled = true
    registry_url = "https://registry.example.com/index.json"
    publisher_key = "<base64 ed25519 public key>"           # verifies bundles+licenses

    [platform]
    accounts_url = "https://accounts.example.com"           # sign-in service (client-side)
    model_proxy_url = "https://models.example.com"          # hosted LiteLLM proxy (platform keys)
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from agent_runtime import runtime_paths

log = logging.getLogger("agentd")


@dataclass(frozen=True)
class DistributionProfile:
    """The parsed distribution.toml (pure value object; OPEN() is the no-file default)."""

    product_id: str = "agentd"
    product_name: str = "agentd"
    default_agent: str = ""  # "" => leave config.agent_id alone
    preinstalled_bundles: tuple = ()  # bundle ids installed on first run
    provisioned_plugins: tuple | None = None  # None => ALL plugins provisioned
    store_enabled: bool = True
    registry_url: str = ""  # "" => no registry configured
    publisher_key: str = ""  # base64 ed25519 pubkey ("" => unsigned mode)
    accounts_url: str = ""  # "" => no hosted accounts (BYOK-only install)
    model_proxy_url: str = ""  # "" => no platform model proxy
    source_path: str = ""  # where this profile was loaded from ("" => open)

    @property
    def is_open(self) -> bool:
        return not self.source_path

    @property
    def model_gateway_url(self) -> str:
        """Deprecated compatibility alias for pre-rename callers."""
        return self.model_proxy_url

    def is_provisioned(self, plugin_id: str) -> bool:
        """The Provisioned gate: None = everything; else membership."""
        return self.provisioned_plugins is None or plugin_id in self.provisioned_plugins


OPEN_PROFILE = DistributionProfile()


def parse_profile(data: dict, source_path: str = "") -> DistributionProfile:
    """dict (parsed toml) -> DistributionProfile. Tolerant: unknown keys ignored,
    wrong-typed sections treated as absent — a bad profile degrades to open, loudly."""
    product = data.get("product") if isinstance(data.get("product"), dict) else {}
    provisioning = data.get("provisioning") if isinstance(data.get("provisioning"), dict) else {}
    store = data.get("store") if isinstance(data.get("store"), dict) else {}
    platform = data.get("platform") if isinstance(data.get("platform"), dict) else {}
    plugins = provisioning.get("plugins")
    return DistributionProfile(
        product_id=str(product.get("id") or "agentd"),
        product_name=str(product.get("name") or "agentd"),
        default_agent=str(product.get("default_agent") or ""),
        preinstalled_bundles=tuple(str(b) for b in (product.get("preinstalled_bundles") or [])),
        provisioned_plugins=(tuple(str(p) for p in plugins) if isinstance(plugins, list) else None),
        store_enabled=bool(store.get("enabled", True)),
        registry_url=str(store.get("registry_url") or ""),
        publisher_key=str(store.get("publisher_key") or ""),
        accounts_url=str(platform.get("accounts_url") or "").rstrip("/"),
        model_proxy_url=str(
            platform.get("model_proxy_url") or platform.get("model_gateway_url") or ""
        ).rstrip("/"),
        source_path=source_path,
    )


def load_profile(path: Path | None = None) -> DistributionProfile:
    """Load the first distribution.toml found (explicit arg > env > user home >
    packaged flavor file), or the OPEN profile when none exists."""
    candidates = [path] if path else runtime_paths.distribution_candidates()
    for candidate in candidates:
        if candidate and candidate.is_file():
            try:
                data = tomllib.loads(candidate.read_text(encoding="utf-8"))
                profile = parse_profile(data, source_path=str(candidate))
                log.info("distribution profile: %s (%s)", profile.product_name, candidate)
                return profile
            except Exception as e:  # noqa: BLE001 — a bad profile never bricks the app
                log.warning("ignoring bad distribution.toml at %s: %s", candidate, e)
    return OPEN_PROFILE

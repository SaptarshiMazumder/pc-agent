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
    app_agent = "figure-creator"          # set => boot straight into that agent's own ui/
    preinstalled_bundles = ["figure-creator"]
    icon = "icon.ico"                     # relative to this file
    app_id = "dev.agentd.app.figure-creator"

    [provisioning]
    plugins = ["core_fs", "shell", "web", "figures", ...]   # omit => all

    [store]
    enabled = true
    registry_url = "https://registry.example.com/index.json"   # READ side: where agents come from
    publish_url = "https://api.example.com"                    # WRITE side: the publish service
    publisher_key = "<base64 ed25519 public key>"           # verifies bundles+licenses

    [platform]
    accounts_url = "https://accounts.example.com"           # sign-in service (client-side)
    model_proxy_url = "https://models.example.com"          # hosted LiteLLM proxy (platform keys)

TWO READERS, ONE DOCUMENT. The desktop shell parses this same file in
``clients/desktop/src/main/flavor.ts`` and reads three keys the daemon has no use for:
``app_agent``, ``product.icon`` and ``product.app_id``. They are parsed here anyway, because the
alternative is what this module used to do — silently drop them. That mattered the moment
something needed to WRITE the file (see ``render_profile``): a writer built from the fields this
reader knew about would have produced a product with no ``app_agent``, i.e. an installer that
opens the generic client instead of the agent it was built for, with nothing in the file to
suggest anything went wrong.
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
    # --- read by the DESKTOP SHELL, not by the daemon (see the module docstring) -----
    # "" => the generic client. Set => the shell boots that agent's own ui/ as the product window.
    app_agent: str = ""
    icon: str = ""  # product icon, relative to this file ("" => the shell's default)
    app_id: str = ""  # Windows AppUserModelID ("" => the shell derives one from product_id)
    store_enabled: bool = True
    registry_url: str = ""  # "" => no registry configured
    # The WRITE side of that registry: the publish service an author's client POSTs to. Baked into
    # the build for the same reason accounts_url is — an author never edits configuration. They
    # install the app, sign in, and press Publish; the product already knows where its marketplace
    # is. "" => this build cannot publish, which is the correct default for a plain checkout.
    publish_url: str = ""
    publisher_key: str = ""  # base64 ed25519 pubkey ("" => unsigned mode)
    accounts_url: str = ""  # "" => no hosted accounts (BYOK-only install)
    model_proxy_url: str = ""  # "" => no platform model proxy
    # "" => the diagnostics uploader can never run, whatever the user's toggle says. A build with
    # nowhere to send diagnostics must not offer to send them.
    ingest_url: str = ""
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
        app_agent=str(product.get("app_agent") or ""),
        icon=str(product.get("icon") or ""),
        app_id=str(product.get("app_id") or ""),
        store_enabled=bool(store.get("enabled", True)),
        registry_url=str(store.get("registry_url") or ""),
        publish_url=str(store.get("publish_url") or "").rstrip("/"),
        publisher_key=str(store.get("publisher_key") or ""),
        accounts_url=str(platform.get("accounts_url") or "").rstrip("/"),
        model_proxy_url=str(
            platform.get("model_proxy_url") or platform.get("model_gateway_url") or ""
        ).rstrip("/"),
        ingest_url=str(platform.get("ingest_url") or "").rstrip("/"),
        source_path=source_path,
    )


def render_profile(profile: DistributionProfile, header: str = "") -> str:
    """DistributionProfile -> the text of a ``distribution.toml``. The INVERSE of parse_profile.

    Why this belongs here and not in the tool that needed it: a writer lived for months in
    ``clients/desktop/scripts/gen-app-flavor.mjs``, hand-assembling the same document in
    JavaScript from string concatenation. Two independent spellings of one format, in two
    languages, neither aware of the other — so a key added to the reader was invisible to the
    writer, and a key the writer emitted could be one the reader silently ignored. Round-tripping
    against ``parse_profile`` in a single test is only possible with both halves in one module.

    ``source_path`` is deliberately NOT emitted: it records where a profile was *read from*, which
    is a property of the load, not of the document.

    Emits only what differs from the defaults, so a generated file states the decisions that were
    actually made rather than a wall of empty strings.
    """
    import json  # only for JSON-compatible TOML string/array quoting

    def q(value: str) -> str:
        return json.dumps(str(value))

    def arr(values) -> str:
        return "[" + ", ".join(q(v) for v in values) + "]"

    lines: list[str] = []
    if header:
        lines += [f"# {line}" if line else "#" for line in header.splitlines()] + [""]

    product = [f"id = {q(profile.product_id)}", f"name = {q(profile.product_name)}"]
    if profile.app_agent:
        product.append(f"app_agent = {q(profile.app_agent)}")
    if profile.default_agent:
        product.append(f"default_agent = {q(profile.default_agent)}")
    if profile.preinstalled_bundles:
        product.append(f"preinstalled_bundles = {arr(profile.preinstalled_bundles)}")
    if profile.icon:
        product.append(f"icon = {q(profile.icon)}")
    if profile.app_id:
        product.append(f"app_id = {q(profile.app_id)}")
    lines += ["[product]", *product]

    if profile.provisioned_plugins is not None:
        lines += ["", "[provisioning]", f"plugins = {arr(profile.provisioned_plugins)}"]

    store = []
    if not profile.store_enabled:
        store.append("enabled = false")
    if profile.registry_url:
        store.append(f"registry_url = {q(profile.registry_url)}")
    if profile.publish_url:
        store.append(f"publish_url = {q(profile.publish_url)}")
    if profile.publisher_key:
        store.append(f"publisher_key = {q(profile.publisher_key)}")
    if store:
        lines += ["", "[store]", *store]

    platform = []
    if profile.accounts_url:
        platform.append(f"accounts_url = {q(profile.accounts_url)}")
    if profile.model_proxy_url:
        platform.append(f"model_proxy_url = {q(profile.model_proxy_url)}")
    if profile.ingest_url:
        platform.append(f"ingest_url = {q(profile.ingest_url)}")
    if platform:
        lines += ["", "[platform]", *platform]

    return "\n".join(lines) + "\n"


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

"""Products — "an agent, shipped as its own app".

A PRODUCT is one agent presented as a standalone desktop application: its name in the Start menu,
its icon, its own taskbar identity, its own entry in Add/Remove Programs. The agent is unchanged;
what differs is the packaging around it.

THE SPLIT THIS MODULE EXISTS FOR. A product used to mean a full copy of the client and the
embedded Python runtime — ~250 MB per agent, so ten agents meant ten runtimes, ten separate
reputations to earn with Windows SmartScreen, and a 250 MB upload every time an author published.
It also meant only someone with node, electron-builder and a prebuilt CPython tree could produce
one, which rules out an author working in a browser. So a product is now two artifacts:

    ENGINE   the shared client + embedded daemon. ONE binary, ONE code-signing certificate,
             installed once, serving every agent on the machine.
    PAYLOAD  ~50 KB: a distribution.toml, the agent's .agentpkg, and an icon. Selected at
             runtime with `--app-dir`, which is what makes one engine *be* this agent.
    STUB     a small installer that ensures the engine, writes the payload, and makes a shortcut.

Everything here is the PURE part of that: what a product IS, derived from what the agent already
declares. No filesystem, no subprocess, no knowledge of NSIS or of any registry.

WHY IT IS IN PYTHON, given that the tooling that first needed it was a Node script. These
precedence chains have to run in three places: the author's machine (`agentd product build`),
Agent Builder's publish tool, and — the one that decides it — a publish SERVICE, where there is
no node, no electron-builder and no checkout. A second implementation of "what version is this
product?" is not a hypothetical drift risk; it already happened. One agent shipped three
different versions of itself simultaneously (agent.toml 1.1.0, the registry 1.0.0, the installer
0.1.5) because three places each decided the answer independently. Installs supersede BY VERSION,
so the practical effect was that authors published updates nobody ever received.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.distribution import DistributionProfile

# ── Installer naming ────────────────────────────────────────────────────────────────────
#
# There is nothing inside a .exe to read an agent id out of, so the link between an installer file
# and the bundle it belongs to is a naming convention:
#
#     <bundle-id>-<version>-setup<ext>        e.g. game-master-0.3.0-setup.exe
#
# Spelled once, here, because a writer and a reader that disagree about it produce a marketplace
# card offering some other agent's download. (`infrastructure/marketplace/index_builder` is the
# reader; it imports this rather than keeping a second copy.)
INSTALLER_MARKER = "-setup"

# The shared engine's reserved id in a registry. RESERVED, because a stub built from a registry row
# under this id will download and RUN that file: nothing else may ever publish here.
ENGINE_BUNDLE_ID = "agentd-engine"


def installer_filename(bundle_id: str, version: str, suffix: str) -> str:
    """The one place the installer naming convention is spelled."""
    return f"{bundle_id}-{version}{INSTALLER_MARKER}{suffix.lower()}"


class ProductError(ValueError):
    """This agent cannot become a product. The message is user-renderable."""


@dataclass(frozen=True)
class EngineRef:
    """WHICH shared engine build a stub installs when the machine has none.

    ``sha256`` is not decoration. A stub downloads a ~250 MB executable and then RUNS it, which is
    the most dangerous step in the whole flow, and NSIS cannot verify an ed25519 signature. So the
    digest is pinned into the stub at BUILD time — read from the signed registry index — and
    checked at INSTALL time. State the bound honestly: that defends against a corrupted or swapped
    download, not against a registry that was already compromised when the stub was built.
    Authenticode on the engine closes the rest, and that is a purchase, not code.

    This is also the wire shape of the index's ``engine`` block, deliberately: the thing the
    registry publishes and the thing a stub is built from must not be two types that can disagree.
    """

    platform: str  # "win" | "mac" | "linux"
    version: str = ""
    url: str = ""  # absolute, or relative to the index location (same rule as a bundle url)
    size: int = 0
    sha256: str = ""
    sig: str = ""  # base64 ed25519 over this asset's sha256

    @property
    def usable(self) -> bool:
        """Enough to build a stub that can install it AND check what it downloaded."""
        return bool(self.url and self.sha256)


@dataclass(frozen=True)
class PlatformEndpoints:
    """Where a HOSTED product signs in and gets model access.

    Both or neither. A product with an accounts url but no model proxy would show a sign-in
    prompt and then fail every model call, which is worse than being honestly BYOK.
    """

    accounts_url: str = ""
    model_proxy_url: str = ""

    @property
    def hosted(self) -> bool:
        return bool(self.accounts_url and self.model_proxy_url)


@dataclass(frozen=True)
class ProductDefaults:
    """Install-wide fallbacks for things an agent does not declare about itself.

    An agent.toml says what the AGENT is. None of it can say which backend this build signs into
    or which engine build is current — those belong to whoever is doing the shipping, so they
    arrive from config here rather than being written into every agent by hand.
    """

    platform: PlatformEndpoints = field(default_factory=PlatformEndpoints)
    # The lowest engine that can run payloads built now. Empty => "any engine", which is the
    # correct default: the engine<->payload contract is additive-only, so a payload is expected to
    # keep working on every later engine. A value here is a claim that a payload uses something a
    # specific engine introduced, and it makes a stub refuse rather than open a broken window.
    engine_min_version: str = ""
    app_id_prefix: str = "dev.agentd.app"
    version_fallback: str = "1.0.0"


@dataclass(frozen=True)
class ProductSpec:
    """One agent, as a shippable app. Everything needed to write a payload and build a stub."""

    agent_id: str
    product_id: str
    name: str
    version: str
    app_id: str
    # Icon paths RELATIVE TO THE AGENT DIRECTORY, in precedence order. A tuple rather than a
    # resolved path because deciding which one exists is a filesystem question, and this module
    # has no filesystem. The writer takes the first that is actually there.
    icon_candidates: tuple[str, ...] = ()
    platform: PlatformEndpoints = field(default_factory=PlatformEndpoints)
    engine_min_version: str = ""
    description: str = ""
    publisher: str = ""  # shown in Add/Remove Programs ("" => blank, which most authors are)

    @property
    def hosted(self) -> bool:
        return self.platform.hosted

    def installer_filename(self, suffix: str = ".exe") -> str:
        """What this product's stub installer is called, by the convention above."""
        return installer_filename(self.agent_id, self.version, suffix)

    def to_profile(self, icon: str = "") -> DistributionProfile:
        """The distribution.toml this product ships, as the value object that PARSES that file.

        Going through DistributionProfile rather than assembling text means the document a product
        ships is, by construction, a document the daemon and the desktop shell can both read —
        checked by a round-trip test instead of by hoping.

        ``icon`` is the file name actually written into the payload ("" when the agent has none);
        the candidates on this spec are only preferences until someone looks at the disk.
        """
        return DistributionProfile(
            product_id=self.product_id,
            product_name=self.name,
            # app_agent is THE knob. It switches the shared shell out of the generic client and
            # into this agent's own ui/ as the product window.
            app_agent=self.agent_id,
            default_agent=self.agent_id,
            preinstalled_bundles=(self.agent_id,),
            icon=icon,
            app_id=self.app_id,
            # A single-agent product has no store: browsing a marketplace inside an app that IS
            # one marketplace listing is a different product's feature.
            store_enabled=False,
            accounts_url=self.platform.accounts_url,
            model_proxy_url=self.platform.model_proxy_url,
        )


@dataclass(frozen=True)
class ProductOverrides:
    """Caller-supplied wins over everything the agent declares.

    Its reason for existing is THIRD-PARTY INTAKE: building a product from a published .agentpkg
    when the agent directory is not on this machine at all. There is no agent.toml to read, so the
    name and version have to come from somewhere, and that somewhere must not be a guess.
    """

    name: str = ""
    version: str = ""
    icon: str = ""  # a path relative to the agent dir, tried before the agent's own candidates
    platform: PlatformEndpoints | None = None  # None => inherit the defaults
    engine_min_version: str = ""


def _first(*values: object) -> str:
    """The first non-empty, stripped value — a precedence chain in one expression."""
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


class ProductRules:
    """agent.toml (+ install defaults) -> ProductSpec. Pure; every branch is a stated precedence.

    A class rather than a function so it can be injected and, later, subclassed by a build that
    wants a different app-id namespace without editing this file.
    """

    def __init__(self, defaults: ProductDefaults | None = None):
        self._defaults = defaults or ProductDefaults()

    @property
    def defaults(self) -> ProductDefaults:
        return self._defaults

    def derive(
        self,
        agent_id: str,
        agent_toml: dict | None = None,
        overrides: ProductOverrides | None = None,
    ) -> ProductSpec:
        """Raises ProductError when this agent cannot be a product.

        ``agent_toml`` empty means intake-from-package: nothing local to read, so ``overrides``
        must supply the name. That is the only case where the [app] requirement is waived, and it
        is waived because the check cannot be performed, not because it stopped applying.
        """
        agent_id = (agent_id or "").strip()
        if not agent_id:
            raise ProductError("a product needs an agent id")
        toml = agent_toml or {}
        over = overrides or ProductOverrides()
        app = toml.get("app") if isinstance(toml.get("app"), dict) else None

        if toml and app is None:
            raise ProductError(
                f"'{agent_id}' has no [app] section in agent.toml, so it has no window to open. "
                "Only app agents can become products — give it a ui/ (scaffold_ui) and an [app] "
                "table with entry = 'ui/index.html'."
            )
        if not toml and not over.name:
            raise ProductError(
                f"no agent.toml for '{agent_id}' — building from a package instead needs an "
                "explicit product name (there is nothing on this machine to read one from)."
            )
        app = app or {}

        name = _first(over.name, app.get("title"), toml.get("name"), agent_id)
        version = _first(over.version, toml.get("version"), self._defaults.version_fallback)
        # The Windows AppUserModelID. Its job is to give each product its own taskbar button and
        # its own jump list; without a distinct one, every agent's window collapses into a single
        # button belonging to whichever product registered first.
        app_id = _first(app.get("app_id"), f"{self._defaults.app_id_prefix}.{agent_id}")

        # [app] icon first (an explicit declaration), then the conventional file. Both relative to
        # the agent dir. Duplicates are dropped so a declaration of "icon.ico" is not tried twice.
        candidates: list[str] = []
        for candidate in (over.icon, app.get("icon"), "icon.ico"):
            text = str(candidate or "").strip().replace("\\", "/")
            if text and text not in candidates:
                candidates.append(text)

        return ProductSpec(
            agent_id=agent_id,
            # A product id distinct from the agent id, because they are different things and one
            # machine holds both: the agent 'weather' installed in ~/.agentd/agents, and the
            # product 'weather-app' installed in Programs.
            product_id=f"{agent_id}-app",
            name=name,
            version=version,
            app_id=app_id,
            icon_candidates=tuple(candidates),
            platform=over.platform if over.platform is not None else self._defaults.platform,
            engine_min_version=_first(
                over.engine_min_version, self._defaults.engine_min_version
            ),
            description=_first(toml.get("description")),
            publisher=_first(app.get("publisher"), toml.get("publisher")),
        )

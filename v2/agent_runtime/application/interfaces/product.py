"""Product-build ports — the four seams between "what a product is" and "how one is made".

All ``Protocol``s (structural, DIP): ``BuildProductService`` depends on these shapes and never on
NSIS, on a zip writer, or on where the engine happens to be hosted. That is not architecture for
its own sake — it is the requirement. The same service has to run in three places:

  * an author's machine       (`agentd product build`, NSIS present, the agent dir on disk)
  * Agent Builder's tool      (same machine, driven by a model instead of a shell)
  * a publish SERVICE         (Linux, no agent dir — only the uploaded .agentpkg)

The third is why the seams exist. A service can be handed a cross-compiling ``StubBuilder`` and a
package-only ``ProductSource`` without the orchestration learning anything about either.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_runtime.domain.product import EngineRef, ProductSpec

# Re-exported so an adapter importing the ports it implements gets every type in those signatures
# from one place. EngineRef is DOMAIN (see domain/product.py) because it is also the wire shape of
# the registry's engine block — the published description and the build input must be one type.
__all__ = [
    "AgentPacker",
    "EngineCatalog",
    "EngineRef",
    "PayloadManifest",
    "PayloadWriter",
    "ProductSource",
    "ProductSourceReader",
    "StubBuilder",
]


@dataclass(frozen=True)
class ProductSource:
    """Where the agent's content comes from: a directory to pack, or an already-packed bundle.

    Exactly one is set. Package-only is not a degenerate case to be tolerated — it is the shape
    the publish service always sees, and the shape a marketplace uses to offer an installer for an
    agent that was authored on someone else's machine.
    """

    agent_dir: Path | None = None
    package: Path | None = None

    def __post_init__(self) -> None:
        if bool(self.agent_dir) == bool(self.package):
            raise ValueError("ProductSource needs exactly one of agent_dir / package")

    @property
    def is_package(self) -> bool:
        return self.package is not None


@dataclass(frozen=True)
class PayloadManifest:
    """What a written payload actually contains — the ~50 KB that makes an engine be this agent."""

    dir: Path
    package: str  # the .agentpkg file name inside bundles/
    icon: str = ""  # "" => this product ships no icon and inherits the engine's
    files: tuple[str, ...] = ()  # every file written, relative to `dir`, for reporting

    @property
    def size(self) -> int:
        return sum(p.stat().st_size for p in self.dir.rglob("*") if p.is_file())


@runtime_checkable
class AgentPacker(Protocol):
    """agent directory -> a .agentpkg in ``out_dir``.

    A port rather than a direct call so the build service is testable without zipping anything,
    and so the one real implementation stays the same code path `agentd bundle pack` uses. What
    gets shipped inside a product must be byte-for-byte what gets published to the marketplace;
    two packers would eventually disagree about what a bundle contains.
    """

    def pack(self, agent_dir: Path, out_dir: Path, version: str = "") -> Path:
        """Returns the written package path. Raises ValueError with a user-facing message."""
        ...


@runtime_checkable
class ProductSourceReader(Protocol):
    """Reads the agent's own declaration out of whichever kind of source this is.

    Split from ``PayloadWriter`` because deriving the spec has to happen BEFORE anything is
    written — the derivation can refuse (no [app] section), and refusing after creating a payload
    directory leaves half a product on disk.
    """

    def declaration(self, source: ProductSource) -> tuple[str, dict]:
        """-> (agent_id, the parsed agent.toml). ``{}`` when the source carries no declaration."""
        ...


@runtime_checkable
class PayloadWriter(Protocol):
    """Materialises the payload directory for one product."""

    def write(self, spec: ProductSpec, source: ProductSource, out_dir: Path) -> PayloadManifest:
        """Write distribution.toml + bundles/<pkg> + icon into ``out_dir``. Replaces its contents.

        ``out_dir`` is fully derived output and is expected to be regenerated, never authored.
        """
        ...


@runtime_checkable
class StubBuilder(Protocol):
    """Builds the small per-product installer.

    ``available()`` returns "" when this builder can run here, and otherwise the REASON it cannot
    — so a caller can report "no makensis on PATH; install NSIS" instead of failing inside a
    subprocess. The distinction matters because the payload is still worth producing on a machine
    that cannot build a stub: it is the part that actually carries the agent.
    """

    platform: str  # which EngineRef platform this builder targets
    suffix: str  # the installer's file extension, e.g. ".exe"

    def available(self) -> str:
        """"" if usable; else a human-readable reason it is not."""
        ...

    def build(
        self,
        spec: ProductSpec,
        payload: PayloadManifest,
        engine: EngineRef,
        out_path: Path,
    ) -> Path:
        """Build the installer at ``out_path`` and return it. Raises ValueError on failure."""
        ...


@runtime_checkable
class EngineCatalog(Protocol):
    """Answers "which engine build should a stub install, and how do I verify it?".

    Deliberately a lookup and not a constant. The engine is released independently of every
    product, so a stub built today has to be able to name the engine that is current today —
    which means this is read from configuration or from the signed registry index, and never
    baked into code.
    """

    def resolve(self, platform: str) -> EngineRef | None:
        """The engine for ``platform``, or None when this install knows of none."""
        ...

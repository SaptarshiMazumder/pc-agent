"""FsProductSourceReader — get an agent's own declaration out of a directory OR a .agentpkg.

The package case is the one that matters. A publish service never has the agent directory; it has
the uploaded bundle and nothing else. The declaration it needs is nevertheless right there — a
.agentpkg carries the whole agent tree under ``agent/``, agent.toml included — so reading it out
of the zip means intake needs no flags, no temp directory, and no second source of truth for the
product's name and version.

The alternative, which this replaces, required an explicit ``--name`` for every package-only build
and had no way to check the [app] section at all. So a bundle with no window could be turned into
an installer that opened nothing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_runtime.application.interfaces.product import ProductSource
from agent_runtime.infrastructure.marketplace import bundle_io, packer


class FsProductSourceReader:
    """Reads from the local filesystem. The only ``ProductSourceReader`` there is so far."""

    AGENT_TOML = "agent.toml"

    def declaration(self, source: ProductSource) -> tuple[str, dict]:
        if source.agent_dir is not None:
            agent_dir = Path(source.agent_dir)
            if not agent_dir.is_dir():
                raise ValueError(f"not a directory: {agent_dir}")
            return agent_dir.name, packer.load_agent_toml(agent_dir)

        package = Path(source.package)  # ProductSource guarantees one of the two is set
        if not package.is_file():
            raise ValueError(f"no such package: {package}")
        # The manifest is the authority on the bundle ID; the agent id inside a package is the
        # bundle id by construction (unpack_bundle writes agents/<bundle id>/).
        manifest = bundle_io.read_manifest(package)
        raw = bundle_io.read_agent_file(package, self.AGENT_TOML)
        if raw is None:
            # A bundle with no agent.toml is unusual but not impossible (a hand-assembled zip).
            # Fall back to what the manifest states rather than refusing: the manifest is the
            # publisher-facing declaration of the same three fields.
            return manifest.id, {
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
            }
        try:
            declared = tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise ValueError(f"{package.name}: agent/agent.toml is not valid TOML: {e}") from e
        # The MANIFEST's version wins over the agent.toml inside the zip. They are normally the
        # same value (packing copies it), but the manifest is what the registry indexes and what
        # installs supersede by — so a product built from a package must claim the version the
        # marketplace will serve, not a number that only exists inside the archive.
        declared.setdefault("name", manifest.name)
        declared["version"] = manifest.version or str(declared.get("version") or "")
        return manifest.id, declared

    # ---- used by the payload writer, which needs bytes rather than a declaration ----
    def icon_bytes(self, source: ProductSource, relative: str) -> bytes | None:
        """The icon file named by ``relative``, from wherever this source keeps it."""
        if source.agent_dir is not None:
            candidate = Path(source.agent_dir) / relative
            if not candidate.is_file():
                return None
            try:
                return candidate.read_bytes()
            except OSError:
                return None
        return bundle_io.read_agent_file(Path(source.package), relative)

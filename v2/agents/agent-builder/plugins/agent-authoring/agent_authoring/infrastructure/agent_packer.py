"""AgentPacker — the only thing in the packaging path that touches disk or zips.

Delegates the actual zip to the core (``bundle_io.pack_bundle``): one packer, so a bundle built
by chat is byte-identical to one built by ``agentd bundle pack``. This adapter contributes the
three things the core packer expects a caller to have already worked out:

    * the agent's ``bundle.toml`` (if the publisher hand-wrote one) as a plain dict
    * the resolved directory of every SHARED plugin the bundle vendors
    * the output directory

It deliberately does NOT write a ``bundle.toml`` into the agent directory. That was tempting —
identity living next to the agent — but it would recreate the bug this phase exists to kill:
``bundle.toml`` outranks ``agent.toml``, so a generated one would pin the version at whatever it
was when first packed, and every later ``version`` bump in ``agent.toml`` would be silently
ignored. ``pack_bundle`` already writes the manifest INTO the zip, which is the copy that
matters. A ``bundle.toml`` in the agent dir stays what it always was: an optional, human-authored
declaration of publisher-facing facts.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_runtime.infrastructure.marketplace import bundle_io


class MissingVendoredPlugin(LookupError):
    """A bundle.toml declares source = "vendored" for a plugin no plugin root contains."""


class AgentPacker:
    def __init__(self, config):
        self._config = config

    # ------------------------------------------------------------------ reads
    def bundle_table(self, agent_dir: Path) -> dict:
        """The ``[bundle]`` table of an optional, hand-authored ``agents/<id>/bundle.toml``.
        ``{}`` when absent or unparseable — packing must not require the file to exist."""
        path = agent_dir / "bundle.toml"
        try:
            return tomllib.loads(path.read_text(encoding="utf-8")).get("bundle") or {}
        except (OSError, ValueError):
            return {}

    def private_plugin_ids(self, agent_dir: Path) -> tuple[str, ...]:
        """The agent's OWN plugins (agents/<id>/plugins/<pid>/plugin.toml). Reported, not
        declared as dependencies — they are already inside the packed agent/ tree."""
        plugins = agent_dir / "plugins"
        if not plugins.is_dir():
            return ()
        return tuple(
            sorted(p.name for p in plugins.iterdir() if (p / "plugin.toml").is_file())
        )

    # ------------------------------------------------------------------ pack
    def pack(self, agent_dir: Path, out_dir: Path, manifest) -> tuple[Path, str]:
        """Write ``<out_dir>/<id>-<version>.agentpkg``. Returns ``(path, sha256)``.

        Raises ``MissingVendoredPlugin`` when a declared vendored plugin is nowhere on this
        install — packing a bundle whose dependency is absent produces an artifact that fails
        on the installer's machine, which is strictly worse than refusing here.
        """
        vendored = self._resolve_vendored(manifest)
        path = bundle_io.pack_bundle(agent_dir, out_dir, manifest, vendored)
        return path, bundle_io.sha256_file(path)

    def _resolve_vendored(self, manifest) -> dict[str, Path]:
        roots = [
            Path(r)
            for r in (
                getattr(self._config, "plugins_dir", ""),
                getattr(self._config, "builtin_plugins_dir", ""),
            )
            if r
        ]
        out: dict[str, Path] = {}
        for dep in manifest.plugins:
            if dep.source != "vendored":
                continue  # "pip" installs on the target; "builtin" just asserts presence
            found = next((r / dep.id for r in roots if (r / dep.id / "plugin.toml").is_file()), None)
            if found is None:
                raise MissingVendoredPlugin(
                    f"bundle.toml declares vendored plugin '{dep.id}', but no plugin root has it "
                    f"({', '.join(str(r) for r in roots) or 'no plugin roots configured'})"
                )
            out[dep.id] = found
        return out

    # ------------------------------------------------------------------ paths
    def default_out_dir(self) -> Path:
        """Where a packed bundle lands when the caller names no directory: ``<state_dir>/dist``.
        Inside the daemon's own state so it exists, is writable, and survives in packaged mode —
        a repo-relative ``dist/`` would be wrong on an installed machine."""
        return Path(getattr(self._config, "state_dir", ".")) / "dist"

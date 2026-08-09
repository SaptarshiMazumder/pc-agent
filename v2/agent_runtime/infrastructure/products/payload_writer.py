"""FsPayloadWriter — write the ~50 KB that turns the shared engine into one specific agent's app.

    <payload>/
      distribution.toml          rendered from DistributionProfile, so the file a product ships
                                 is by construction one both readers can parse
      bundles/<id>-<ver>.agentpkg   the agent itself
      icon.ico                   optional

REGENERATED, NEVER AUTHORED. The directory is cleared first. A payload that accumulated leftovers
across builds would ship a stale .agentpkg alongside the fresh one, and the engine installs
whichever it finds — so "why is my agent the old version?" would have no visible cause.

THE ICON IS NORMALISED to ``icon.ico``. An agent may declare its icon anywhere in its own tree
(``[app] icon = "assets/brand/logo.ico"``); inside a payload it always lands under one name, so
the engine and the stub both know it without reading the toml to find out.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agent_runtime.application.interfaces.product import PayloadManifest, ProductSource
from agent_runtime.distribution import render_profile
from agent_runtime.domain.product import ProductSpec

PAYLOAD_ICON = "icon.ico"
BUNDLES_DIR = "bundles"
DISTRIBUTION_FILE = "distribution.toml"
PACKAGE_SUFFIX = ".agentpkg"


def _as_agentpkg(name: str) -> str:
    """The name a package must have inside a payload. Appended rather than replaced, so a source
    called `x-setup.exe` becomes `x-setup.exe.agentpkg` — visibly odd, which beats invisible."""
    return name if name.endswith(PACKAGE_SUFFIX) else f"{name}{PACKAGE_SUFFIX}"

_HEADER = """{name} — payload for the shared agentd engine. GENERATED; do not edit.

[product] app_agent switches the engine into AGENT-APP mode: it finds or starts the daemon,
installs the bundle beside this file on first run, then opens {agent}'s own ui/ as the
product window. Run it with:  <Engine>.exe --app-dir <this directory>"""


class FsPayloadWriter:
    """:param packer: an ``AgentPacker`` — used only when the source is an agent DIRECTORY.
    :param reader: the source reader, for pulling an icon out of a package."""

    def __init__(self, packer, reader):
        self._packer = packer
        self._reader = reader

    def write(self, spec: ProductSpec, source: ProductSource, out_dir: Path) -> PayloadManifest:
        out_dir = Path(out_dir)
        # Fully derived output: clear it. `ignore_errors` because a previous run interrupted
        # mid-write can leave a directory a plain rmtree refuses on Windows.
        shutil.rmtree(out_dir, ignore_errors=True)
        bundles = out_dir / BUNDLES_DIR
        bundles.mkdir(parents=True, exist_ok=True)

        if source.is_package:
            package_src = Path(source.package)
            # THE SUFFIX IS THE CONTRACT, not a convention. The engine finds a payload's agent by
            # globbing `bundles/*.agentpkg`; a file copied in under any other name is simply not
            # there, and the failure is silent and total — the product installs, the window opens,
            # and it has no agent. Forcing it here means no caller can get this wrong, whatever
            # the source file happened to be called.
            package = bundles / _as_agentpkg(package_src.name)
            shutil.copyfile(package_src, package)
        else:
            # The version is NOT passed through: the packer runs the same precedence chain
            # (bundle.toml > agent.toml) that produced spec.version, and forcing the derived value
            # back in would silently override a bundle.toml that deliberately differs.
            package = self._packer.pack(Path(source.agent_dir), bundles)

        icon_name = ""
        for candidate in spec.icon_candidates:
            data = self._reader.icon_bytes(source, candidate)
            if data:
                (out_dir / PAYLOAD_ICON).write_bytes(data)
                icon_name = PAYLOAD_ICON
                break

        profile = spec.to_profile(icon=icon_name)
        header = _HEADER.format(name=spec.name, agent=spec.agent_id)
        (out_dir / DISTRIBUTION_FILE).write_text(
            render_profile(profile, header=header), encoding="utf-8"
        )

        files = sorted(
            p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*") if p.is_file()
        )
        return PayloadManifest(
            dir=out_dir,
            package=package.name,
            icon=icon_name,
            files=tuple(files),
        )

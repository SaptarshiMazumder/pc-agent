"""NsisStubBuilder — compile ``templates/stub.nsi`` into one product's installer.

EVERY VALUE IS A /D DEFINE. The template is never rewritten, string-substituted or templated: it
is compiled as-is and parameterised on the command line. That is deliberate. A generator that
edits its own script cannot be read to find out what it does, cannot be linted, and hides its
mistakes inside a temp file — and this particular script is the one that downloads and runs a
250 MB executable on a stranger's machine.

WORKS OFF WINDOWS. makensis is a plain cross-platform C program (``apt install nsis``), so the
publish service can build Windows installers on Linux. That is the whole reason this is a Python
adapter behind a port instead of another step in the Node build.

``available()`` explains itself rather than failing inside a subprocess: a machine with no NSIS
should still produce a payload and be told, in one sentence, what to install to get an installer
too.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from agent_runtime.application.interfaces.product import PayloadManifest
from agent_runtime.domain.product import EngineRef, ProductSpec

log = logging.getLogger("agentd")

TEMPLATE = Path(__file__).resolve().parent / "templates" / "stub.nsi"


class NsisStubBuilder:
    platform = "win"
    suffix = ".exe"

    def __init__(self, makensis: str = "", timeout: float = 300.0, template: Path | None = None):
        """:param makensis: an explicit path; "" => discovered on PATH (never a hardcoded guess)."""
        self._makensis = makensis.strip()
        self._timeout = timeout
        self._template = Path(template) if template else TEMPLATE

    # ------------------------------------------------------------------ availability
    @staticmethod
    def _cached_by_electron_builder() -> str:
        """electron-builder downloads its own NSIS into a per-user cache. Reuse it.

        Anyone who has built the desktop client already has a working makensis, and asking them to
        install a second copy to build a stub would be pointless friction. The location is derived
        from the OS user directory and the tool's documented cache layout — no machine-specific
        path is assumed, and a miss is simply a miss.
        """
        roots = []
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.append(Path(local) / "electron-builder" / "Cache" / "nsis")
        home = Path.home()
        roots += [
            home / "AppData" / "Local" / "electron-builder" / "Cache" / "nsis",  # win, no env var
            home / ".cache" / "electron-builder" / "nsis",  # linux
            home / "Library" / "Caches" / "electron-builder" / "nsis",  # mac
        ]
        for root in roots:
            if not root.is_dir():
                continue
            # Newest cached NSIS wins; the directory holds one dir per downloaded version.
            for version_dir in sorted(root.iterdir(), reverse=True):
                for relative in ("makensis.exe", "Bin/makensis.exe", "makensis", "bin/makensis"):
                    candidate = version_dir / relative
                    if candidate.is_file():
                        return str(candidate)
        return ""

    def executable(self) -> str:
        return (
            self._makensis
            or shutil.which("makensis")
            or self._cached_by_electron_builder()
        )

    def available(self) -> str:
        if not self._template.is_file():
            return f"the stub template is missing from this install ({self._template})"
        if not self.executable():
            return (
                "makensis was not found (not on PATH, and no electron-builder NSIS cache). Install "
                "NSIS to build installers — Windows: `winget install NSIS.NSIS`; Debian/Ubuntu: "
                "`apt install nsis`; macOS: `brew install makensis`. The payload was still written, "
                "and any machine that already has the engine can run it with --app-dir."
            )
        return ""

    # ------------------------------------------------------------------ build
    def build(
        self,
        spec: ProductSpec,
        payload: PayloadManifest,
        engine: EngineRef,
        out_path: Path,
    ) -> Path:
        reason = self.available()
        if reason:
            raise ValueError(reason)
        if not engine.usable:
            # The service checks this first; asserted again because a stub built without a digest
            # would silently be a stub that runs an unverified download.
            raise ValueError("refusing to build a stub for an engine with no url + sha256")

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload_dir = Path(payload.dir).resolve()

        defines = {
            "PRODUCT_ID": spec.product_id,
            "PRODUCT_NAME": spec.name,
            "PRODUCT_VERSION": spec.version,
            "PRODUCT_PUBLISHER": spec.publisher,
            "AGENT_ID": spec.agent_id,
            "APP_ID": spec.app_id,
            "PAYLOAD_DIR": str(payload_dir),
            "ENGINE_URL": engine.url,
            "ENGINE_SHA256": engine.sha256.lower(),
            "OUT_FILE": str(out_path.resolve()),
        }
        # Optional defines are OMITTED rather than passed empty, because the template branches on
        # `!ifdef` — an empty ENGINE_MIN_VERSION would make it compare against the string "".
        if engine.version:
            defines["ENGINE_VERSION"] = engine.version
        if spec.engine_min_version:
            defines["ENGINE_MIN_VERSION"] = spec.engine_min_version
        icon = payload_dir / payload.icon if payload.icon else None
        if icon and icon.is_file():
            defines["ICON_FILE"] = str(icon)

        command = [self.executable(), "-V2"]
        command += [f"-D{key}={value}" for key, value in defines.items()]
        command.append(str(self._template))

        log.info("building %s stub: %s", spec.product_id, out_path.name)
        try:
            result = subprocess.run(  # noqa: S603 — argv list, no shell
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ValueError(f"makensis could not be run: {e}") from e

        if result.returncode != 0 or not out_path.is_file():
            # makensis puts the actionable line in stdout, not stderr. Both are surfaced because a
            # truncated compiler error is the reason build failures get guessed at.
            detail = (result.stdout or "").strip() or (result.stderr or "").strip()
            raise ValueError(
                f"makensis failed (exit {result.returncode}) building {out_path.name}:\n{detail}"
            )
        return out_path

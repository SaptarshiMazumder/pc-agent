"""NodeToolchain — finding the Node the product ships, and running it.

WHY THE PRODUCT SHIPS ONE AT ALL. An agent's window is a React project: source in ``app/``, built
output in ``ui/``, and the daemon serves only the second. Turning one into the other is
``npm run build`` — so a user who installed this product and built an agent through Agent Builder
would need a toolchain they never agreed to install, to change a window they own. They would edit
``app/src``, see nothing change, and have no way to find out why. The desktop build therefore
bundles Node (clients/desktop/scripts/build-runtime.ps1) and the supervisor puts it on the
daemon's PATH.

RESOLUTION ORDER, and why it is this way round:

  AGENTD_NODE_DIR   the bundled one, named exactly by the supervisor
  PATH              a developer's own, in a checkout that has no bundle

The bundled one wins because it is the one we tested. A user's Node may be years old, and "builds
on the author's machine and not on the user's" is the entire class of failure the bundle exists to
end.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandOutput:
    """What a command did. `ok` is the exit code being zero and nothing else."""

    ok: bool
    output: str
    #: True when the command was killed for running too long, so the caller can say so rather
    #: than reporting an empty failure.
    timed_out: bool = False


class NodeMissing(Exception):
    """No Node anywhere. Carries the message the user should see verbatim."""


class NodeToolchain:
    """Runs ``node`` and ``npm``. Knows where they are; knows nothing about agents."""

    def __init__(self, env: dict | None = None):
        #: Injected so a test can point this at a fixture without touching os.environ.
        self._env = dict(env if env is not None else os.environ)

    # ------------------------------------------------------------------ finding

    def node_dir(self) -> str:
        """The directory holding node/npm, or '' when there is none."""
        bundled = (self._env.get("AGENTD_NODE_DIR") or "").strip()
        if bundled and self._executable(Path(bundled)):
            return bundled
        found = shutil.which("node", path=self._env.get("PATH"))
        return str(Path(found).parent) if found else ""

    def available(self) -> bool:
        return bool(self.node_dir())

    @staticmethod
    def _executable(directory: Path) -> Path | None:
        """The node binary inside a directory, whichever name this platform uses.

        The two official layouts differ: the Windows zip puts ``node.exe`` at the root of the
        tree, every other platform puts ``bin/node`` a level down. The supervisor already resolves
        that and hands over the BIN directory, so both spellings are checked here only to stay
        honest if it ever hands over the root instead.
        """
        for name in ("node.exe", "node"):
            candidate = directory / name
            if candidate.is_file():
                return candidate
        return None

    def require(self) -> str:
        """The node directory, or raise with something a user can act on."""
        directory = self.node_dir()
        if directory:
            return directory
        raise NodeMissing(
            "no Node.js available, so an agent's window cannot be built. The desktop product "
            "ships one; a source checkout uses yours. Install Node 18+ and retry, or run this "
            "from the packaged app."
        )

    # ------------------------------------------------------------------ running

    def npm(self, args: list[str], cwd: Path, timeout: float = 600.0) -> CommandOutput:
        """Run npm in `cwd`. Combined stdout+stderr, because a build failure writes to both and
        showing half of it is worse than showing none."""
        directory = self.require()
        # The bundled Node is PREPENDED rather than appended: an npm script that shells out to
        # `node` must reach the same one that started it, or a build resolves half its toolchain
        # from a different install.
        env = dict(self._env)
        env["PATH"] = f"{directory}{os.pathsep}{env.get('PATH', '')}"
        npm = shutil.which("npm", path=env["PATH"])
        if not npm:
            raise NodeMissing(
                f"found node at {directory} but no npm beside it — the bundled Node tree is "
                f"incomplete. Rebuild it: scripts/build-runtime.ps1"
            )
        return self._run([npm, *args], cwd=cwd, env=env, timeout=timeout)

    @staticmethod
    def _run(command: list[str], cwd: Path, env: dict, timeout: float) -> CommandOutput:
        try:
            done = subprocess.run(
                command,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                # npm on Windows is a .cmd shim, which CreateProcess cannot execute directly.
                shell=os.name == "nt",
            )
        except subprocess.TimeoutExpired as e:
            partial = (e.stdout or "") + (e.stderr or "")
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", "replace")
            return CommandOutput(
                ok=False,
                output=partial.strip() or "(no output before the timeout)",
                timed_out=True,
            )
        return CommandOutput(
            ok=done.returncode == 0,
            output=((done.stdout or "") + (done.stderr or "")).strip(),
        )

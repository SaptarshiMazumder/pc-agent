"""ConfinedCommand — start a shell command on the daemon's own box that can only see its account.

WHAT USES IT. A hosted daemon's BACKGROUND shell (`exec` with background=true, managed by
`process`). Foreground commands go to the executor's microVM instead; a background command
cannot, because a microVM lives exactly as long as one call and there would be nothing left to
poll. So a background command runs here, beside every other account's files, and three locks
keep it inside its own:

  1. LANDLOCK (landlock_confinement.py): the run's own read roots readable, its write clamp
     writable, the system folders a command needs readable, a scratch folder of its own. No
     other path opens — the model's reflexive `ls /data` lists only the caller's files.
  2. NO CAPABILITIES (setpriv): the daemon runs as root, so a child that kept root's privileges
     could read the daemon's own environment through /proc and with it our internal keys.
     Dropping every capability blocks that while the child keeps uid 0 — which it needs, since
     the account's files on the shared volume are owned by the daemon's user.
  3. A CLEAN ENVIRONMENT: the command gets the few variables a shell needs and the ones the
     agent passed, never the daemon's. (Built by the caller: see `environment`.)

TWO HALVES OF ONE PROTOCOL, IN ONE CLASS. The daemon builds the argv (`argv`); the launched
interpreter parses it back (`main`), applies the locks and execs the command. Keeping both here
means the flag format cannot drift between the side that writes it and the side that reads it.

WHAT IT DOES NOT ENFORCE: an agent's own write_denies and protected paths inside its account
(the fs tools check those per write; Landlock can only grant, not carve holes out of a grant).
The boundary this module holds is the one between ACCOUNTS.
"""

from __future__ import annotations

import os
import shutil
import sys

from agent_runtime.infrastructure.tools.sandbox.landlock_confinement import LandlockConfinement

#: System folders a shell command needs to read and execute. Missing ones are skipped by the
#: confinement (an image without /lib64 is not an error).
SYSTEM_READ_ONLY = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt")

#: Device files a command reads or writes as a matter of course (`> /dev/null`, random bytes).
DEVICE_READ_WRITE = ("/dev/null", "/dev/zero", "/dev/full")
DEVICE_READ_ONLY = ("/dev/urandom", "/dev/random")

#: The one variable set from the daemon's environment that a command genuinely needs.
_INHERITED = ("PATH",)

_SETPRIV = "/usr/bin/setpriv"
_SETPRIV_FLAGS = (
    "--no-new-privs",
    "--inh-caps=-all",
    "--bounding-set=-all",
    # Keeps uid 0 from regaining capabilities on exec — without these, dropping the bounding set
    # is undone the moment the shell starts.
    "--securebits=+noroot,+noroot_locked,+keep_caps_locked",
)

_ENTRYPOINT = "agent_runtime.infrastructure.tools.sandbox.confined_command"


class ConfinedCommand:
    """One background command, confined to an account's roots plus a scratch folder."""

    def __init__(self, read_roots: tuple[str, ...], write_roots: tuple[str, ...],
                 scratch_dir: str) -> None:
        self._read_roots = tuple(p for p in read_roots if p)
        self._write_roots = tuple(p for p in write_roots if p)
        self._scratch = scratch_dir

    # ------------------------------------------------------------------ daemon side

    def argv(self, command: str) -> list[str]:
        """The process to start: this module's entrypoint, told what to allow, then the command."""
        out = [sys.executable, "-m", _ENTRYPOINT]
        # The interpreter running the entrypoint must stay readable after the lock is applied
        # (it is still executing), so its install tree is granted like a system folder.
        for path in (*SYSTEM_READ_ONLY, sys.prefix, sys.base_prefix, *self._read_roots,
                     *DEVICE_READ_ONLY):
            out += ["--ro", path]
        for path in (*self._write_roots, self._scratch, *DEVICE_READ_WRITE):
            out += ["--rw", path]
        return [*out, "--", command]

    def environment(self, home: str, extra: dict[str, str]) -> dict[str, str]:
        """What the command sees. Nothing of the daemon's beyond PATH, then the agent's own."""
        env = {k: os.environ[k] for k in _INHERITED if k in os.environ}
        env.update({
            "HOME": home,
            "TMPDIR": self._scratch,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TERM": "dumb",
        })
        env.update(extra)
        return env

    def discard_scratch(self) -> None:
        """Delete the scratch folder once the command has exited. Its contents were the
        command's own temporary files; nothing else reads them."""
        shutil.rmtree(self._scratch, ignore_errors=True)

    # ------------------------------------------------------------------- child side

    @staticmethod
    def main(args: list[str]) -> None:
        """Parse the argv `argv()` built, lock this process, and become the command."""
        read_only: list[str] = []
        read_write: list[str] = []
        i = 0
        while i < len(args) and args[i] != "--":
            flag, value = args[i], args[i + 1]
            (read_only if flag == "--ro" else read_write).append(value)
            i += 2
        command = " ".join(args[i + 1:])
        LandlockConfinement(read_only, read_write).apply()
        os.execv(_SETPRIV, [_SETPRIV, *_SETPRIV_FLAGS, "--", "/bin/sh", "-c", command])


if __name__ == "__main__":
    ConfinedCommand.main(sys.argv[1:])


__all__ = ["ConfinedCommand"]

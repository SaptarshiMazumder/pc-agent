"""EnvFile — the user's ``.env``, rewritten one key at a time.

Secrets reach this daemon through a single channel: the ``.env`` beside ``agentd.config.json``,
read into the process environment at boot by ``config._load_dotenv``. Anything that needs to
CHANGE one while the daemon is running — a provider key saved from Settings, the credentials
written at sign-in — goes through here.

Two properties are easy to lose and both are load-bearing:

  * EVERY OTHER LINE SURVIVES. This file is hand-edited and holds keys this code has never heard
    of. Rewriting it wholesale would silently delete them.
  * THE CHANGE IS LIVE. LiteLLM reads provider keys from the environment at call time, so a key
    written here takes effect on the next call with no restart.

Lifted out of ``presentation/gateway.py``, where it was a module-level function with four call
sites. Writing files is infrastructure's job; a transport layer reaching through to the disk is
the kind of thing that is fine until the day something else needs the same behaviour.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("agentd")


class EnvFile:
    """:param path: the ``.env`` to maintain. It need not exist yet."""

    def __init__(self, path: Path):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def read(self, name: str) -> str:
        """The live value of one key.

        Reads the ENVIRONMENT rather than the file, deliberately: boot loads the file into
        ``os.environ`` and every write here updates both, so the environment is the current truth
        — and it also sees values that never came from this file at all (a real exported variable,
        a container secret, a systemd unit). Reading the file instead would report "not set" for a
        key that is very much set.
        """
        return (os.environ.get(name) or "").strip()

    def update(self, keys: dict) -> bool:
        """Set or clear keys, preserving every other line, and apply them to ``os.environ``.

        An empty value removes the key. Returns True when the file was written; False means the
        write failed and was logged — the environment is left untouched in that case, so the
        in-memory and on-disk states cannot disagree.
        """
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines() if self._path.is_file() else []
        except OSError:
            lines = []
        remaining = dict(keys)
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                name = stripped.split("=", 1)[0].strip()
                if name in remaining:
                    val = remaining.pop(name)
                    if val == "":
                        continue  # delete this line
                    out.append(f"{name}={val}")
                    continue
            out.append(line)
        for name, val in remaining.items():
            if val != "":
                out.append(f"{name}={val}")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
        except OSError as e:  # noqa: BLE001 — reported to the caller, which surfaces it
            log.warning("could not write env file %s: %s", self._path, e)
            return False
        for name, val in keys.items():
            if val == "":
                os.environ.pop(name, None)
            else:
                os.environ[name] = val
        return True

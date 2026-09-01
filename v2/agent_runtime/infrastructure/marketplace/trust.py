"""Client-side trust for a schema-2 registry: verify the roster, then remember it.

Two pieces, both small, both fail-closed:

  * ``verify_roster`` — is this roster genuinely signed by the key this install pins? Everything
    downstream (which creators exist, which keys they use) is derived from the answer, so a
    roster that does not verify yields NO trusted keys rather than a partially trusted set.
  * ``RosterMemory`` — the newest ``issued`` this install has ever accepted, per registry.

WHY THE MEMORY EXISTS. Bundle signatures make forgery impossible; they do nothing about REPLAY.
Someone who can rewrite index.json (a compromised bucket, a hostile mirror, anyone in the middle
of a plain-http fetch) cannot invent a creator or a bundle, but they can serve last month's index
verbatim — signatures and all — and quietly un-revoke a creator you revoked yesterday, or hide a
security update by pinning the store to an older listing. Remembering the newest roster accepted
turns that from silent into a refusal.

WHAT IT DOES NOT CATCH, so the guarantee is not overstated: a FIRST fetch has nothing to compare
against, and an attacker who serves a stale roster to a brand-new install is not detected by this.
It bounds the window rather than closing it; a signed index manifest with a monotonic serial is
the actual fix and is deliberately not in this change.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent_runtime.domain.bundle import PublisherRoster, roster_signing_payload
from agent_runtime.infrastructure import signing

log = logging.getLogger("agentd")


def verify_roster(roster: PublisherRoster | None, root_key_b64: str) -> bool:
    """True iff this roster is signed by the pinned root key. No key pinned => nothing to check."""
    if roster is None:
        return False
    if not root_key_b64:
        return True  # unpinned install: signatures are informational (same rule as schema 1)
    if not roster.sig:
        return False
    payload = roster_signing_payload(roster.entries, roster.revoked, roster.issued)
    return signing.verify(root_key_b64, payload, roster.sig)


class RosterMemory:
    """The newest roster `issued` accepted per registry URL. A tiny JSON file, written rarely.

    Never raises: a machine where this file cannot be read or written still installs bundles, it
    just loses the replay bound. Refusing to work because a cache file is unwritable would trade a
    partial guarantee for a total outage.
    """

    def __init__(self, path: Path | None) -> None:
        self._path = Path(path) if path else None

    def newest_seen(self, registry_url: str) -> str:
        return str(self._read().get(registry_url) or "")

    def remember(self, registry_url: str, issued: str) -> None:
        if not (self._path and issued):
            return
        data = self._read()
        if str(data.get(registry_url) or "") >= issued:
            return
        data[registry_url] = issued
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError as e:
            log.debug("registry trust: cannot record the roster date (%s)", e)

    def is_downgrade(self, registry_url: str, issued: str) -> bool:
        """A roster OLDER than one already accepted from this registry — refuse it.

        Compared as ISO-8601 strings, which sort correctly as text precisely because they are
        fixed-width and most-significant-first. Parsing them into datetimes here would add a
        failure mode (a malformed date) to a check whose whole job is to be unambiguous.
        """
        seen = self.newest_seen(registry_url)
        return bool(seen and issued and issued < seen)

    def _read(self) -> dict:
        if not (self._path and self._path.is_file()):
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

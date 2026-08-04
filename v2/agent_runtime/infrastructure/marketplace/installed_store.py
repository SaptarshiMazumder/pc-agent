"""InstalledStore — the JSON ledger of installed bundles (atomic writes).

Lives at <state_dir>/installed_bundles.json so it travels with the install's state
(and a purge of state honestly forgets what was installed)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent_runtime.domain.bundle import InstalledBundle

log = logging.getLogger("agentd")


class JsonInstalledStore:
    def __init__(self, path: Path):
        self._path = path

    def list(self) -> list[InstalledBundle]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        bundles = []
        for raw in data.get("bundles", []):
            try:
                bundles.append(
                    InstalledBundle(
                        id=str(raw["id"]),
                        version=str(raw.get("version") or "0"),
                        installed_at=str(raw.get("installed_at") or ""),
                        source=str(raw.get("source") or ""),
                        plugin_ids=tuple(str(p) for p in raw.get("plugin_ids") or []),
                        entitlement=str(raw.get("entitlement") or ""),
                    )
                )
            except (KeyError, TypeError):
                log.warning("installed_bundles.json: skipping malformed row %r", raw)
        return bundles

    def installed_ids(self) -> frozenset[str] | None:
        """Just the ids — i.e. exactly the agents that arrived inside a ``.agentpkg``
        (bundle id IS agent id; ``unpack_bundle`` writes to ``agents_dir/<manifest.id>``).

        Distinct from ``list()`` because this feeds a TRUST decision (the plugin sandbox's
        untrusted set), where "no ledger" and "unreadable ledger" must not look alike:

          * missing file  -> ``frozenset()`` — nothing has been installed, which is normal
          * unreadable / malformed -> ``None`` — the caller must fail CLOSED

        ``list()`` collapses both to ``[]``, which is right for a roster and wrong here: a
        corrupt ledger would silently promote downloaded code to first-party.
        """
        if not self._path.exists():
            return frozenset()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("installed_bundles.json unreadable — treating agent tools as untrusted")
            return None
        return frozenset(
            str(raw["id"])
            for raw in data.get("bundles", [])
            if isinstance(raw, dict) and raw.get("id")
        )

    def get(self, bundle_id: str) -> InstalledBundle | None:
        return next((b for b in self.list() if b.id == bundle_id), None)

    def record(self, bundle: InstalledBundle) -> None:
        bundles = [b for b in self.list() if b.id != bundle.id] + [bundle]
        self._write(bundles)

    def remove(self, bundle_id: str) -> None:
        self._write([b for b in self.list() if b.id != bundle_id])

    def _write(self, bundles: list[InstalledBundle]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "bundles": [
                {
                    "id": b.id,
                    "version": b.version,
                    "installed_at": b.installed_at,
                    "source": b.source,
                    "plugin_ids": list(b.plugin_ids),
                    "entitlement": b.entitlement,
                }
                for b in sorted(bundles, key=lambda b: b.id)
            ],
        }
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)

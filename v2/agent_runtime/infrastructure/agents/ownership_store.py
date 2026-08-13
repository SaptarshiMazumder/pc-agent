"""Read/write the per-agent ownership record (domain/ownership.py) — the IO half.

The record sits INSIDE the agent's definition dir (`agents/<id>/.agentd-meta.json`) rather than
in a central ledger, deliberately: the definition dir is the unit everything else moves — packs,
installs, uninstalls, account overlays, backups — and a sidecar ledger is a second source of
truth that drifts the first time one of those moves happens without it. The packers exclude the
file, so a published bundle never carries its author's record; the installer writes a fresh one
on arrival.

Best-effort by design. A read that fails is "no record" (legacy rules apply, see the domain
module); a write that fails must never fail the create/install that triggered it — the agent
works either way, ownership just stays legacy-derived until something re-stamps it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent_runtime.domain import ownership

log = logging.getLogger("agentd")


def read(agent_dir: Path | str | None) -> ownership.OwnershipRecord | None:
    if not agent_dir:
        return None
    path = Path(agent_dir) / ownership.RECORD_FILENAME
    try:
        return ownership.parse_record(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def write(agent_dir: Path | str, record: ownership.OwnershipRecord) -> None:
    path = Path(agent_dir) / ownership.RECORD_FILENAME
    try:
        path.write_text(
            json.dumps(ownership.record_dict(record), indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        log.warning("ownership: could not stamp %s — legacy rules will apply", path)


def stamp_create(agent_dir: Path | str, account_id: str | None, hosted: bool) -> None:
    """A freshly AUTHORED agent: whoever is here right now made it."""
    owner = ownership.stamp_owner(account_id, hosted)
    write(agent_dir, ownership.OwnershipRecord(owner=owner, origin=ownership.AUTHORED))


def stamp_install(
    agent_dir: Path | str,
    account_id: str | None,
    hosted: bool,
    source_id: str,
    source_version: str,
) -> None:
    """A marketplace arrival: the installer owns the COPY; provenance names the original.
    The platform installing (seeds, web-app syncs) is what `curated` means — one derivation,
    in the domain, so no call site can say it differently."""
    owner = ownership.stamp_owner(account_id, hosted)
    write(
        agent_dir,
        ownership.OwnershipRecord(
            owner=owner,
            origin=ownership.origin_for_install(owner),
            source_id=source_id,
            source_version=source_version,
        ),
    )

"""Who an agent BELONGS to — a stored fact, never a guess from folder location.

Step 2 of planning/platform/identity-ownership-statelessness.md. Step 1 derived "is this agent
the caller's" from which layer it sat in, which was right enough to stop the bleeding and wrong
in the way layout-derived answers always are: move a directory, restore a backup, seed a
container, and the answer silently changes. So the answer becomes a RECORD the runtime writes
next to the definition — `agent.toml` stays the author's file; this one is the platform's.

Three owners, three origins, one membership test:

    owner   an account id, "local" (the person whose machine this is), or "platform" (us)
    origin  "authored" (they made it) | "installed" (marketplace copy) | "curated" (ours)
    mine    = the record's owner is among the identities the caller may act as

The caller's identities differ by deployment in exactly one way that matters: on a DESKTOP the
human at the keyboard is the operator of the machine, so they act as "local" even while signed
in — signing in must never take away what was already theirs. On a HOSTED daemon a signed-in
stranger acts only as their account, and the operator (machine token, no account) acts as the
platform.

Record-less directories are LEGACY, and legacy must keep meaning what it meant: everything in a
checkout or a pre-record install behaves exactly as Step 1 left it, via `presumed_owner` — the
owner such a directory would have been stamped with had the stamp existed.

Pure: values in, values out. IO lives in infrastructure/agents/ownership_store.py.
"""

from __future__ import annotations

from dataclasses import dataclass

# The record's filename, part of the platform contract: packers EXCLUDE it (a published bundle
# carries the author's files; the installer writes a fresh record on arrival), and authors never
# edit it.
RECORD_FILENAME = ".agentd-meta.json"

LOCAL_OWNER = "local"
PLATFORM_OWNER = "platform"

AUTHORED = "authored"
INSTALLED = "installed"
CURATED = "curated"
# The platform HOSTING a copy so a /apps/<id> URL works — which is not the platform ENDORSING
# it. A curated agent is in everyone's list because the operator chose it; a web-app copy
# exists because some author published with web=true, and conflating the two let any publish
# push itself into every user's sidebar. Reachable and Store-listed, never auto-listed.
WEB_APP = "web-app"

_ORIGINS = (AUTHORED, INSTALLED, CURATED, WEB_APP)


@dataclass(frozen=True)
class OwnershipRecord:
    owner: str
    origin: str = AUTHORED
    source_id: str = ""  # registry id, when origin == "installed"/"curated"
    source_version: str = ""


def parse_record(raw: object) -> OwnershipRecord | None:
    """A record from JSON, or None when there isn't one worth trusting.

    Tolerant on purpose: this file travels with state dirs across versions, and a half-readable
    record must degrade to "no record" (legacy rules) rather than to a crash or — worse — to an
    owner nobody is."""
    if not isinstance(raw, dict):
        return None
    owner = str(raw.get("owner") or "").strip()
    if not owner:
        return None
    origin = str(raw.get("origin") or "").strip().lower()
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    return OwnershipRecord(
        owner=owner,
        origin=origin if origin in _ORIGINS else AUTHORED,
        source_id=str(source.get("id") or "").strip(),
        source_version=str(source.get("version") or "").strip(),
    )


def record_dict(record: OwnershipRecord) -> dict:
    """The JSON shape on disk. `source` only when there is one — an empty stanza would imply an
    installed provenance the agent does not have."""
    out: dict = {"owner": record.owner, "origin": record.origin}
    if record.source_id:
        out["source"] = {"id": record.source_id, "version": record.source_version}
    return out


def stamp_owner(account_id: str | None, hosted: bool) -> str:
    """Who a runtime write (create / install) belongs to, right now, on this deployment.

    An account when one is signed in; otherwise the deployment's own identity — the platform on
    a hosted daemon (its no-account writes are seeds and web-app syncs, i.e. curation), the
    local owner on a desktop."""
    account_id = (account_id or "").strip()
    if account_id:
        return account_id
    return PLATFORM_OWNER if hosted else LOCAL_OWNER


def origin_for_install(owner: str) -> str:
    """An install is `installed` — unless the platform itself did it, which is what `curated`
    IS. One derivation, so a seed job cannot forget to say so."""
    return CURATED if owner == PLATFORM_OWNER else INSTALLED


def callers(account_id: str | None, hosted: bool) -> frozenset[str]:
    """Every identity the current caller may act as.

    Desktop keeps "local" alongside a signed-in account — the machine is still theirs; signing
    in adds an identity, it must never subtract one. Hosted grants exactly one: the account, or
    (machine token, no account) the platform itself."""
    account_id = (account_id or "").strip()
    if hosted:
        return frozenset({account_id}) if account_id else frozenset({PLATFORM_OWNER})
    return frozenset({account_id, LOCAL_OWNER}) if account_id else frozenset({LOCAL_OWNER})


def presumed_owner(in_overlay: bool, account_id: str | None, hosted: bool) -> str:
    """The owner a RECORD-LESS directory would have been stamped with — the legacy rule.

    An overlay dir exists only because its account put something there; the shared layer is the
    deployment's (the platform's on hosted, the local owner's on a desktop). This is exactly the
    Step-1 layer rule, restated as an owner so one membership test serves both worlds."""
    if in_overlay:
        return (account_id or "").strip() or (PLATFORM_OWNER if hosted else LOCAL_OWNER)
    return PLATFORM_OWNER if hosted else LOCAL_OWNER


def owned_by_caller(owner: str, account_id: str | None, hosted: bool) -> bool:
    return owner in callers(account_id, hosted)


# The identities that own a DEPLOYMENT itself: the machine's human on a desktop, the platform
# on a hosted daemon. Holding one means the world resident on that deployment is yours.
DEPLOYMENT_OWNERS = frozenset({LOCAL_OWNER, PLATFORM_OWNER})


def may_observe(owner: str, identities: frozenset[str]) -> bool:
    """May a caller holding ``identities`` OBSERVE data owned by ``owner`` — a live run's
    events, a file's bytes?

    Two clauses, and the first is what keeps this branch-free across deployments: a caller
    holding a DEPLOYMENT-owner identity observes everything resident on that deployment —
    the same rule sight of agents already follows (FileAgentRegistry._current: the desktop
    human and the hosted operator "still see everything — it is theirs"). ``callers()`` never
    grants "local" or "platform" to a hosted stranger, so on a shared daemon this collapses
    to strict owner-membership; on a desktop every socket of the machine's human holds
    "local" and the test is always true — today's behaviour, now as a rule instead of an
    accident. An anonymous caller (the public app tier) holds NO identities and observes
    nothing owned by anyone."""
    if identities & DEPLOYMENT_OWNERS:
        return True
    return bool(owner) and owner in identities

"""Per-account state roots (M2) — route a user's chats + files under their OWN subtree.

When an account is active (hosted/accounts mode), that account's transcripts and workspace live
under ``<state_dir>/accounts/<account_id>/agents/<agent_id>/`` — the SAME single-user layout, one
level down. So plain local/desktop use is just the degenerate one-account case, and desktop paths
never change: callers fold this in ONLY when ``accounts.account_id()`` is set, otherwise they use
the agent's own dirs exactly as before.

Deliberately NOT covered here (bigger, separate changes): cron/notify (single sqlite files
partitioned by an ``agent_id`` column). Semantic memory IS partitioned, via
``accounts.memory_partition``.

INSTALLED AGENTS ARE NOW PARTITIONED TOO (``account_agents_dir`` / ``account_plugins_dir``).
The original note here said "the agent CATALOG stays shared", and that was right while the only
agents were the ones WE shipped: a shared catalogue of curated agents is a feature. It stopped
being right the moment the marketplace let a user install one, because installs went to the
daemon-global ``agents_dir`` — so on a hosted daemon one visitor's install appeared for everyone,
and their uninstall removed it from everyone.

The split is now: the CURATED catalogue (whatever the deployment ships in ``config.agents_dir``)
stays shared and read-only, and each account gets an OVERLAY of the agents it installed itself.
FileAgentRegistry unions the two per connection, overlay winning on an id collision, so a user who
installs their own build of a curated agent gets theirs without affecting anybody else's.
"""

from __future__ import annotations

import re
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe(part: str) -> str:
    """Filesystem-safe path segment (account ids are ``acct_<hex>`` already; this is defensive)."""
    cleaned = _UNSAFE.sub("_", str(part or "")).strip("._")
    return cleaned or "unknown"


def account_root(state_dir, account_id: str) -> Path:
    """One account's root: ``<state_dir>/accounts/<acct>``. Per-agent data, projects.json, and
    project workspaces all hang under here (the whole single-user world, one level down)."""
    return Path(state_dir) / "accounts" / _safe(account_id)


def org_root(state_dir, org_id: str) -> Path:
    """One ORGANIZATION's root: ``<state_dir>/orgs/<org_id>`` (tenancy E3).

    A SIBLING namespace to ``accounts/``, never inside it: an org holds shared agent
    DEFINITIONS its members read, while every member's chats/workspace/memory stay under their
    OWN account subtree — the definitions-shared / data-private split that keeps two employees
    using the company agent invisible to each other."""
    return Path(state_dir) / "orgs" / _safe(org_id)


def org_agents_dir(state_dir, org_id: str) -> Path:
    """An org's shared agents: ``<state_dir>/orgs/<org_id>/agents`` — one folder per agent,
    definition only (agent.toml, ui/, plugins/, skills/), exactly the curated-catalogue shape.
    Runtime data never lands here: every run routes its transcripts/workspace through
    ``account_agent_dir`` under the CALLING member's own subtree."""
    return org_root(state_dir, org_id) / "agents"


def identity_root(state_dir, identity: str) -> Path:
    """WHERE one identity's world lives — the identity→root mapper the tenancy plan names.

    ``org_*`` ids map to ``orgs/``; everything else is an account under ``accounts/``. Exists
    because ``_file_roots_for`` (and anything else walking an identity SET) would otherwise
    hand an org id to ``account_root`` and silently mint ``accounts/<org_id>`` — two namespaces
    conflated into one folder, which is the quiet kind of tenancy bug."""
    from agent_runtime.domain import ownership

    if ownership.is_org(identity):
        return org_root(state_dir, identity)
    return account_root(state_dir, identity)


def account_agent_dir(state_dir, account_id: str, agent_id: str | None) -> Path:
    """The per-account root for one agent's data: ``<state_dir>/accounts/<acct>/agents/<agent>``."""
    return account_root(state_dir, account_id) / "agents" / _safe(agent_id or "main")


def account_state_dir(state_dir, account_id: str, agent_id: str | None) -> Path:
    """Where this account's transcripts for ``agent_id`` live (SessionStore appends ``/sessions``)."""
    return account_agent_dir(state_dir, account_id, agent_id)


def account_workspace(state_dir, account_id: str, agent_id: str | None) -> Path:
    """Where this account's file/exec/shell tools for ``agent_id`` bind."""
    return account_agent_dir(state_dir, account_id, agent_id) / "workspace"


def account_agents_dir(state_dir, account_id: str) -> Path:
    """This account's agents: ``<state_dir>/accounts/<acct>/agents``.

    ONE FOLDER PER AGENT, holding everything that agent is and everything it has done —
    ``agents/<id>/`` is its definition (agent.toml, ui/, plugins/, skills/) AND its
    ``workspace/`` AND its ``sessions/``. Exactly the shape of the shared catalogue, one level
    down, which is what makes desktop-local, desktop-cloud and hosted the same layout with
    different roots.

    It used to be split: definitions under ``installed/agents/<id>/`` and data under
    ``agents/<id>/``. Two trees for one agent meant every reader had to know which half it
    wanted, an authored agent lived in a directory labelled "installed" (which read as "not
    yours to edit" and stopped an agent-builder mid-task), and nothing about the split earned
    its cost. ``account_agent_dir`` now resolves INSIDE this root, so the two functions
    describe one place.
    """
    return account_root(state_dir, account_id) / "agents"


def account_agents_layers(state_dir) -> list[tuple[str, Path]]:
    """EVERY account's installed-agents layer on this deployment: ``[(account_id, dir), ...]``,
    account-sorted, existing accounts only.

    For PROCESS-GLOBAL work that must see all layers rather than the calling connection's one:
    private-plugin discovery builds one daemon-wide tool map, and app serving's
    deployment-owner view answers for the whole machine. A layer that only loads when its
    owner happens to be the caller is a layer whose contents work by coincidence."""
    root = Path(state_dir) / "accounts"
    try:
        names = sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    return [(name, account_agents_dir(state_dir, name)) for name in names]


LEGACY_INSTALLED_DIR = "installed"  # the pre-unification home of definitions + vendored plugins


def migrate_legacy_account_layout(state_dir) -> list[str]:
    """Fold every account's legacy ``installed/`` tree into the unified one. -> migrated ids.

    Runs at daemon boot, sweeps ``accounts/*``, and is IDEMPOTENT: an account with no
    ``installed/`` is skipped, so the second boot does nothing and the thousandth costs one
    ``is_dir``.

    MERGE RULE: an entry that already exists in the destination is LEFT ALONE and the legacy
    copy is dropped. The destination is where the live data has been accumulating (transcripts,
    workspace files); the legacy tree holds definitions plus, occasionally, an empty
    ``workspace/`` created at scaffold time. Overwriting on collision would trade a real
    workspace for an empty one — the one outcome nobody could undo.

    Never raises: a migration failure must not stop a daemon from starting. It logs, leaves the
    legacy tree exactly as it found it, and that account keeps whatever already resolved.
    """
    import logging
    import shutil

    log = logging.getLogger("agentd")
    migrated: list[str] = []
    root = Path(state_dir) / "accounts"
    try:
        accounts = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return migrated
    for account in accounts:
        legacy = account / LEGACY_INSTALLED_DIR
        if not legacy.is_dir():
            continue
        try:
            for kind in ("agents", "plugins"):
                source = legacy / kind
                if not source.is_dir():
                    continue
                destination = account / kind
                destination.mkdir(parents=True, exist_ok=True)
                for item in sorted(source.iterdir()):
                    target = destination / item.name
                    if target.exists():
                        # Definition arriving where data already lives: move the definition's
                        # own entries in, again keeping whatever is already there.
                        if item.is_dir() and target.is_dir():
                            for inner in sorted(item.iterdir()):
                                if not (target / inner.name).exists():
                                    shutil.move(str(inner), str(target / inner.name))
                            shutil.rmtree(item, ignore_errors=True)
                        continue
                    shutil.move(str(item), str(target))
                shutil.rmtree(source, ignore_errors=True)
            if not any(legacy.iterdir()):
                legacy.rmdir()
            migrated.append(account.name)
            log.info("user_state: migrated %s to the unified agents layout", account.name)
        except OSError as e:  # noqa: PERF203 — one bad account must not abort the sweep
            log.warning("user_state: could not migrate %s (%s) — left as-is", account.name, e)
    return migrated


def _org_definition_grants(state_dir, org_ids) -> list[str]:
    """READ grants for the caller's orgs: every org agent's DEFINITION VIEW (its folder minus
    ``USER_DATA_DIRS``), per agent — the same shape the account's own agent gets, applied to
    each shared one. Granted per-entry rather than the org root wholesale as defence in depth:
    the org tree is definition-only by construction (see ``org_agents_dir``), and this keeps
    that true even if a stray data folder ever lands there. A non-member's set simply contains
    no org ids, so the org root is never in their scope — which is what "no two enterprises
    step on each other" means at the filesystem."""
    from agent_runtime.domain.agent import definition_entries

    grants: list[str] = []
    for org_id in org_ids or ():
        root = org_agents_dir(state_dir, org_id)
        try:
            agent_dirs = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for d in agent_dirs:
            grants.extend(str(e) for e in definition_entries(d))
    return grants


def tenant_scope(
    config, account_id: str, agent_dir, workspace, org_ids: tuple[str, ...] = ()
) -> tuple[tuple, tuple]:
    """The filesystem this run may SEE and the boundary it may WRITE inside:
    ``(read_roots, write_clamp)`` — the per-run values behind ``check_read`` and
    ``check_write``'s clamp (application/write_scope).

    LIVES HERE because this module already owns the answer to "where does an account's world
    sit inside the one store" — a second module deriving tenant boundaries from the same layout
    is a second chance to disagree with it.

    THE MODE NEVER APPEARS DOWNSTREAM. A non-hosted daemon (desktop local AND desktop cloud —
    one human, their own machine) gets ``((), ())``: empty means unrestricted, so the checks
    pass everything and behaviour is byte-for-byte what it was. A hosted daemon gets values:

      signed-in  read = own account subtree + this agent's definition view + the shared
                 catalogue/plugin roots (+ ``config.hosted_read_roots`` extras)
                 write = own account subtree (+ the run's effective workspace)
      anonymous  read = the definition view + the run's workspace + the shared roots;
                 write = the workspace only — a public visitor touches no account, ever

    The agent's DEFINITION VIEW (its folder minus ``USER_DATA_DIRS``) is granted rather than
    its folder: on the unified layout the folder also holds whatever its OWNER's runs have
    produced, which is exactly the data a stranger using the same agent must never see.
    Sessions appear in NO read grant — transcripts are reachable only through session RPCs,
    which check ``may_observe``. Deny-lists don't exist here on purpose: everything is a
    positive grant, so what was never granted needs nobody to remember to deny it.
    """
    if not getattr(config, "hosted", False):
        return ((), ())
    from agent_runtime.application.write_scope import NOTHING
    from agent_runtime.domain.agent import definition_entries

    shared = [
        str(p)
        for p in (
            getattr(config, "agents_dir", "") or "",
            getattr(config, "plugins_dir", "") or "",
            getattr(config, "builtin_plugins_dir", "") or "",
            *(getattr(config, "hosted_read_roots", None) or ()),
        )
        if str(p).strip()
    ]
    ws = [str(workspace)] if str(workspace or "").strip() else []
    definition = list(definition_entries(agent_dir)) if agent_dir else []
    if account_id:
        own = str(account_root(config.state_dir, account_id))
        # THE ORG LAYER (tenancy E3): members may READ their orgs' shared agent definitions —
        # and only read. The write clamp below never widens: org agents are installed by the
        # share RPC, not edited in place by members' runs.
        org_defs = _org_definition_grants(config.state_dir, org_ids)
        reads = (own, *ws, *definition, *org_defs, *shared)
        clamp = (own, *ws)
    else:
        reads = (*ws, *definition, *shared)
        # No workspace resolved for an anonymous caller => nothing is writable. An empty clamp
        # means UNRESTRICTED (the desktop degenerate case), so fail closed with a root that can
        # never contain a real path instead.
        clamp = tuple(ws) or (NOTHING,)
    # order-preserving dedupe, same idiom as _expand_paths: a scope listing a dir twice reads
    # like a bug when it shows up in a refusal message.
    return tuple(dict.fromkeys(reads)), tuple(dict.fromkeys(clamp))


def account_plugins_dir(state_dir, account_id: str) -> Path:
    """Plugins vendored by this account's installs. Same overlay idea as ``account_agents_dir``.

    Per-account rather than shared because a bundle carries its own plugins: two accounts on
    different versions of the same agent would otherwise fight over one plugin directory, and the
    loser would be whoever installed first.
    """
    return account_root(state_dir, account_id) / "plugins"


def account_config_file(state_dir, account_id: str) -> Path:
    """This account's CONFIG OVERLAY: ``<state_dir>/accounts/<acct>/config.json``.

    The daemon's own ``config.json`` is the MASTER — deployment-owned, and on a hosted daemon it
    describes a machine that thousands of accounts share. It cannot also be the file a user edits
    when they change their model, because ``config.set`` wrote it AND hot-applied the result to
    the live Config: one tenant's Save changed the brain for everyone, instantly.

    So a user's edits land here instead, and the config a run reads is ``master ⊕ this overlay``
    (infrastructure/account_config.py). The overlay holds ONLY the keys that account actually
    changed, which is what makes "seeded from the latest master" stay true: raise a default in the
    master and every user who never overrode it moves with you, while everyone who did keeps
    theirs. A full copy taken at signup would freeze each user on the day they registered.

    Inside the account root on purpose — the subtree ``tenant_scope`` already fences and
    ``ownership.may_observe`` already guards — so the overlay inherits the isolation that is
    already enforced instead of introducing a second notion of whose file this is.
    """
    return account_root(state_dir, account_id) / "config.json"

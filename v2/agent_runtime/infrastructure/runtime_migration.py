"""Idempotent ownership-preserving migration out of editable agent definitions.

Known ownership goes into the private runtime namespace. Ambiguous legacy data is
retained in the account's private legacy archive, never guessed into an org's scope.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

from agent_runtime.domain.agent import USER_DATA_DIRS, USER_DATA_FILES
from agent_runtime.infrastructure import secret_storage
from agent_runtime.infrastructure.agents import ownership_store

log = logging.getLogger("agentd.migration")


def _rewrite_artifact_paths(sessions: Path, before: str, after: str) -> None:
    """Keep historical chat downloads working after their private files move."""
    def rewrite(value):
        if isinstance(value, str):
            # Preserve references only to this workspace, never similarly named siblings.
            if value == before or value.startswith(before + "/") or value.startswith(before + "\\"):
                return after + value[len(before):]
            return value
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value
    for path in sessions.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.suffix not in (".json", ".jsonl"):
            continue
        fd, temporary = tempfile.mkstemp(prefix=".migration-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target, path.open(encoding="utf-8") as source:
                if path.suffix == ".json":
                    text = source.read()
                    try:
                        target.write(json.dumps(rewrite(json.loads(text)), ensure_ascii=False) + "\n")
                    except ValueError:
                        target.write(text)
                else:
                    for line in source:
                        try:
                            target.write(json.dumps(rewrite(json.loads(line)), ensure_ascii=False) + "\n")
                        except ValueError:
                            target.write(line)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def migrate(config) -> dict:
    root = Path(config.state_dir).resolve()
    moved, archived = 0, 0
    catalogue = Path(config.agents_dir)
    for account in (root / "accounts").glob("acct_*"):
        if account.is_symlink() or not account.is_dir():
            continue
        for definition in (account / "agents").glob("*"):
            if definition.is_symlink() or not definition.is_dir():
                continue
            record = ownership_store.read(definition)
            owner = str(getattr(record, "owner", "") or "")
            if (definition / "agent.toml").is_file():
                namespace = "personal"
            elif re.fullmatch(r"org_[A-Za-z0-9_-]+", owner):
                namespace = owner
            elif (catalogue / definition.name / "agent.toml").is_file() or definition.name == "main":
                namespace = "platform"
            else:
                namespace = ""
            destination = (account / "runtime" / namespace / "agents" / definition.name
                           if namespace else account / "legacy" / "agents" / definition.name)
            destination.resolve().relative_to(account.resolve())
            for name in sorted(USER_DATA_DIRS | USER_DATA_FILES):
                source, target = definition / name, destination / name
                if not source.exists() or source.is_symlink():
                    continue
                # Authoring recreates empty workspace scaffolds in definitions.
                # They contain no legacy data and must not create a new archive
                # conflict on every subsequent daemon restart.
                if source.is_dir() and not any(source.iterdir()):
                    continue
                source.resolve().relative_to(account.resolve())
                target.resolve().relative_to(account.resolve())
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    # Never replace data that already accumulated in the new runtime.
                    target = account / "legacy" / "migration-conflicts" / definition.name / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        # Preserve every generation, including state written by
                        # an older process after its first migration. Existing
                        # runtime data and earlier archives are never replaced.
                        archive = Path(tempfile.mkdtemp(prefix=definition.name + "-", dir=target.parent.parent))
                        target = archive / name
                os.replace(source, target)
                if name == "settings.json":
                    secret_storage.write(target, secret_storage.read(target))
                if name == "workspace":
                    _rewrite_artifact_paths(destination / "sessions", str(source), str(target))
                moved += 1
                archived += int(not namespace)
    # Old global OAuth records have no reliable account ownership. Keep them private,
    # encrypted, and unavailable to the new account-scoped OAuth service.
    for path in (root / "oauth").glob("*/*.json"):
        if not path.is_symlink():
            secret_storage.write(path, secret_storage.read(path))
    if moved:
        log.info("runtime migration: %d entries moved (%d retained in private legacy archives)", moved, archived)
    return {"moved": moved, "archived": archived}

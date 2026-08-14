"""McpApprovalStore — which agent may launch which command, as the user said so.

An agent declaring ``command = ["uvx", "awslabs.aws-api-mcp-server@latest"]`` is asking for
third-party code to be downloaded and executed on this machine, with the user's file access,
because they installed an agent. That is a decision for the person whose machine it is, and it is
recorded here so they are asked once rather than every run.

THE APPROVAL IS OF A COMMAND, NOT OF A NAME. It stores the exact argv, so an update that changes
what the agent launches — a different package, an extra flag, a different registry — needs asking
again. Approving "the aws server" once and having that stand for whatever `aws` means in version
7 would make the prompt theatre.

A ``url`` server is never recorded here: nothing runs locally, so there is nothing to approve.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("agentd")


class McpApprovalStore:
    """:param path: the JSON file to keep. It need not exist yet."""

    def __init__(self, path: Path):
        self._path = Path(path)

    def _load(self) -> dict:
        if not self._path.is_file():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # FAIL CLOSED. An unreadable ledger means nothing is approved, so the worst case is
            # being asked again — the alternative is treating a corrupt file as consent.
            log.warning("mcp_approvals.json unreadable — treating every command as unapproved")
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _key(agent_id: str, name: str) -> str:
        return f"{agent_id}::{name}"

    def approved(self, agent_id: str, name: str, command) -> bool:
        return self._load().get(self._key(agent_id, name)) == list(command)

    def approve(self, agent_id: str, name: str, command) -> bool:
        data = self._load()
        data[self._key(agent_id, name)] = list(command)
        return self._write(data)

    def revoke(self, agent_id: str, name: str) -> bool:
        data = self._load()
        data.pop(self._key(agent_id, name), None)
        return self._write(data)

    def _write(self, data: dict) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except OSError as e:  # noqa: BLE001 — reported to the caller, which surfaces it
            log.warning("could not write %s: %s", self._path, e)
            return False
        return True

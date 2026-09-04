"""AccountSettingsStore — one account's values for one agent's declared `[[settings]]`.

WHAT IT REPLACES, AND WHY. A setting's value used to live in the agent's own
`agent.config.json` and be exported to the process environment as `<agent-id>__KEY`. One file,
one environment, one value — which is correct on a desktop, where "the machine" and "the user"
are the same thing, and wrong the moment a daemon serves more than one person: the first tenant
to open an agent wrote the key every later tenant then ran on. The store below is the same idea
with the missing dimension put back.

    <state_dir>/accounts/<account>/agents/<agent>/settings.json
    { "COMFYUI_URL": "https://abc.proxy.runpod.net", "COMFYUI_AUTH": "Bearer …" }

BESIDE THE THINGS IT BELONGS WITH. That directory already holds this account's `sessions/` and
`workspace/` for this agent — `user_state.account_agent_dir` owns the layout and this asks it
rather than composing a second opinion. On a hosted daemon it lands on the same persistent
volume as those (AGENTD_HOME), so a value survives a restart exactly as a transcript does.

DESKTOP IS NOT A SPECIAL CASE. A desktop run has one account id and takes the identical path;
there is no second code path to keep in step, which is the whole reason this is not a
`hosted_only` branch.

THE AUTHOR'S DEFAULT IS NOT STORED HERE. `[[settings]] default` is a declaration that travels
in `agent.toml`; writing a copy of it into every account's file would freeze the value at first
use and make a later change by the author invisible. The resolver layers instead — see
`run_context.current_setting_value`.

AN EMPTY VALUE IS A DELETION, matching what the settings page has always meant by clearing a
field. Storing "" would be a third state ("set, to nothing") no part of the UI can express —
and, here, one that could not be told apart from "use the author's default".
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("agentd")

#: The file's name inside an account's per-agent directory.
ACCOUNT_SETTINGS_FILE = "settings.json"

#: The account id a daemon with no accounts service uses — a desktop, where "the machine" and
#: "the user" are the same person. Named rather than "": one code path, one layout, and a
#: desktop's file sits exactly where a tenant's does instead of in a second special place.
LOCAL_ACCOUNT = "local"


class AccountSettingsStore:
    """:param state_dir: the daemon's state directory. The account and agent are per call —
    one store object serves every tenant, because the path is data, not construction."""

    def __init__(self, state_dir):
        self._state_dir = str(state_dir or "")

    def path_for(self, account_id: str, agent_id: str) -> Path | None:
        """Where this account's values for this agent live, or None when either is missing.

        NO ACCOUNT, NO FILE. A caller with no account is not "the default account" — it is a
        boot-time or channel caller with no tenant, and inventing a directory for it would put
        one person's key somewhere every later person reads.
        """
        if not self._state_dir or not account_id or not agent_id:
            return None
        from agent_runtime.infrastructure import user_state

        return (
            user_state.account_agent_dir(self._state_dir, account_id, agent_id)
            / ACCOUNT_SETTINGS_FILE
        )

    def read(self, account_id: str, agent_id: str) -> dict:
        """This account's stored values, or ``{}``. Never raises: a broken file must not take
        the agent down with it — it is logged and treated as "nothing stored", which is the
        state the user can fix from the settings page."""
        path = self.path_for(account_id, agent_id)
        if path is None or not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning(
                "account '%s' has an unreadable %s for agent '%s' (%s: %s) — its stored "
                "settings are being IGNORED",
                account_id,
                ACCOUNT_SETTINGS_FILE,
                agent_id,
                type(e).__name__,
                e,
            )
            return {}
        if not isinstance(raw, dict):
            log.warning(
                "account '%s' has a %s for agent '%s' that is not an object — ignoring it",
                account_id,
                ACCOUNT_SETTINGS_FILE,
                agent_id,
            )
            return {}
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}

    def write(self, account_id: str, agent_id: str, values: dict) -> list[str]:
        """Merge values into this account's file. Returns the keys touched.

        WRITTEN WHOLE, so a crash mid-save cannot leave a half-file — the same reason the
        agent's own config is written whole rather than appended.
        """
        path = self.path_for(account_id, agent_id)
        if path is None:
            raise ValueError(
                f"cannot store settings for agent '{agent_id}': no account is signed in"
            )
        table = self.read(account_id, agent_id)
        touched: list[str] = []
        for key, value in (values or {}).items():
            key = str(key)
            text = "" if value is None else str(value)
            if text:
                table[key] = text
            else:
                table.pop(key, None)
            touched.append(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
        return sorted(touched)

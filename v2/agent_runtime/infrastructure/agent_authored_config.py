"""AgentAuthoredConfig — the config an agent's AUTHOR shipped inside it.

WHAT IT IS FOR. An agent could always describe what it was; it could not say how it had to RUN.
So it arrived on a stranger's machine configured by a stranger: an agent that needs a vision model
to be any use at all got whatever that daemon's `model` happened to be, and the author had no way
to say otherwise. `agent.config.json` is that missing sentence, and because it lives INSIDE the
agent directory it travels with the package — install the agent, get the author's choices.

    agents/<id>/agent.config.json
    {
      "user_editable": false,
      "model": "gemini/gemini-3.1-pro-preview",
      "max_turns": 40,
      "settings": { "COMFY_URL": "https://..." }
    }

VALUES ONLY. What each declared setting MEANS — its label, kind, whether it is required — stays in
`agent.toml`'s `[[settings]]`, where it already was. Declaring a schema in two files is how the two
end up disagreeing about which keys exist, and `kind` is what the packer needs in order to know
which of these values is a secret.

IT IS NOT TRUSTED WITH THE MACHINE. This file arrives from whoever wrote the agent, so the
resolver applies a whitelist to it (`agent_config.authored_values`): an author who writes `port`
into it is writing a key nobody reads. The whitelist lives in the domain beside the layering rule
it belongs to, not here.

A MISSING FILE IS THE NORMAL CASE, not a failure. Every agent built before this existed ships
none, and so does every agent whose author had no opinion about how it runs. Both resolve to
exactly what they resolved to before, which is the only reason this could be added at all.

A MALFORMED FILE IS NOT. Unreadable JSON means the author wrote something and it is not being
honoured — silently treating that as "no opinion" would run their agent on somebody else's model
and tell nobody. It is logged loudly, every time it is read.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("agentd")

#: The file's name inside an agent directory. Named here so the packer, the settings page and the
#: resolver cannot disagree about it.
AGENT_CONFIG_FILE = "agent.config.json"

#: Where the agent's declared `[[settings]]` VALUES live inside that file.
SETTINGS_KEY = "settings"


class AgentAuthoredConfig:
    """:param agents_dir_of: ``(agent_id) -> Path | None`` — the registry's own `resolve_dir`.

    INJECTED rather than given a root, because where an agent lives is the registry's business:
    an account overlay puts one somewhere the shared catalogue does not, and a second opinion
    about that here would read the wrong file for exactly the agents that are hardest to debug.
    """

    def __init__(self, agents_dir_of):
        self._dir_of = agents_dir_of

    def path_for(self, agent_id: str) -> Path | None:
        d = self._dir_of(agent_id)
        return Path(d) / AGENT_CONFIG_FILE if d else None

    def read(self, agent_id: str) -> dict:
        """The parsed file, or ``{}`` when the agent ships none.

        NOT CACHED. It is one small file read per run, and the alternative is a cache that has to
        be invalidated by the settings page, by an agent being reinstalled, and by an account
        overlay changing under it — three invalidation paths for a few microseconds.
        """
        path = self.path_for(agent_id)
        if path is None or not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            # LOUD. The author wrote something and it is not being honoured. Returning {} without
            # saying so runs their agent on the daemon's values and looks exactly like an agent
            # that never had an opinion.
            log.warning(
                "agent '%s' has an unreadable %s (%s: %s) — its author's settings are being "
                "IGNORED and it is running on this machine's values instead",
                agent_id,
                AGENT_CONFIG_FILE,
                type(e).__name__,
                e,
            )
            return {}
        if not isinstance(raw, dict):
            log.warning(
                "agent '%s' has a %s that is not an object — ignoring it",
                agent_id,
                AGENT_CONFIG_FILE,
            )
            return {}
        return raw

    def settings(self, agent_id: str) -> dict:
        """The values for this agent's declared `[[settings]]`, as ``{KEY: value}``.

        Separate from the run knobs above because they are a different kind of thing: a knob is a
        preference about how the agent behaves, a setting is something the agent NEEDS in order to
        work — a URL, a token, an account id.
        """
        table = self.read(agent_id).get(SETTINGS_KEY)
        return {str(k): v for k, v in table.items()} if isinstance(table, dict) else {}

    def write_settings(self, agent_id: str, values: dict) -> list[str]:
        """Merge declared-setting values into the agent's own config. Returns the keys written.

        THIS IS WHERE A SETTING NOW LIVES. It used to be a line in the machine's shared `.env`,
        under a prefixed name (`<agent-id>__COMFY_URL`) invented precisely because one file was
        being shared by every agent on the machine. An agent that owns its own config file needs
        no prefix and no sharing: the file is the agent's, so the key is just the key.

        AN EMPTY VALUE REMOVES THE KEY, matching what the settings page has always meant by
        clearing a field — and matching `.env`, where an empty value deleted the line. Storing ""
        would be a third state ("set, to nothing") that no part of the UI can express.

        WRITTEN WHOLE, not appended. json.dumps of the merged dict, so a half-written file is not
        a possible outcome of a crash mid-save the way a line-oriented append is.
        """
        path = self.path_for(agent_id)
        if path is None:
            raise ValueError(f"no agent '{agent_id}' — cannot write its settings")
        current = self.read(agent_id)
        table = dict(current.get(SETTINGS_KEY) or {})
        written: list[str] = []
        for key, value in values.items():
            key = str(key)
            text = "" if value is None else str(value)
            if text:
                table[key] = text
            else:
                table.pop(key, None)
            written.append(key)
        current[SETTINGS_KEY] = table
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        return sorted(written)


def export_settings(agent_dir, agent_id: str, declared_keys) -> list[str]:
    """Put an agent's stored setting values into this process's environment. Returns what it set.

    WHY THE ENVIRONMENT IS STILL INVOLVED. The value has to reach places that can only read an
    environment variable: a sandboxed tool runs in a child process, an `[[mcp]]` command is spawned
    with `${VAR}` substituted, a plugin reads `os.environ`. That was true before this change and is
    true after it. What changed is which file is AUTHORITATIVE — the agent's own config rather than
    a shared `.env` — so the environment stops being the store and becomes what it always actually
    was, the transport.

    THE PREFIX STAYS on the environment side. Two agents may each declare `AWS_ACCESS_KEY_ID` for
    two different accounts, and one process environment cannot hold both under one name. Inside
    the agent's own config there is no such collision, which is why the file stores the bare key.

    IT OVERRIDES what `.env` loaded, and that is the point: the migration is otherwise inert. A
    legacy `<agent-id>__KEY` line would keep winning and the value in the agent's own file would
    never be the one in force. Nothing else writes these prefixed names — they exist only for this.
    """
    from agent_runtime.domain.agent import setting_env_name

    path = Path(agent_dir) / AGENT_CONFIG_FILE
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        table = raw.get(SETTINGS_KEY) if isinstance(raw, dict) else None
    except (OSError, ValueError):
        # The reader above logs this loudly on every read; staying quiet here avoids saying it
        # twice per agent per boot for one broken file.
        return []
    if not isinstance(table, dict):
        return []

    wanted = {str(k) for k in (declared_keys or ())}
    done: list[str] = []
    for key, value in table.items():
        key = str(key)
        # DECLARED ONLY. A stale entry for a field the agent no longer declares must not keep
        # occupying an environment name — and a value nobody declared is not a setting.
        if wanted and key not in wanted:
            continue
        text = "" if value is None else str(value)
        if not text:
            continue
        os.environ[setting_env_name(agent_id, key)] = text
        done.append(key)
    return sorted(done)

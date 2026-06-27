"""Google Workspace plugin — the prompt half.

The TOOLS come from the `workspace-mcp` server declared in plugin.toml ([mcp]). This module
contributes the dynamic **## Google accounts** instruction block that used to be hardcoded in
agentd's core prompt builder — now it lives WITH the plugin, gated and parameterised by the
plugin itself (reads the agent's pinned account). The core prompt builder knows nothing about
Google.

`register(api, ctx)` is called once at load; it registers a prompt-section callable that the
prompt builder invokes each turn with `(tools, agent, config)`.
"""

# A Google MCP tool is namespaced like `google__gmail_send` — but match on intent words too so
# the block shows for any Workspace toolset.
_GOOGLE_HINTS = ("gmail", "drive", "calendar", "google", "workspace", "sheet", "docs")


def _has_google_tools(tools) -> bool:
    return any(any(h in getattr(t, "name", "").lower() for h in _GOOGLE_HINTS) for t in tools)


def google_section(tools, agent, config) -> str:
    """The ## Google accounts block — shown when a Google tool is present (or an account is
    pinned), with the per-agent 'default to X' line. Verbatim port of the old core block."""
    single = (getattr(agent, "google_account", "") if agent else "") or \
        getattr(config, "google_account", "")
    accounts = list(getattr(agent, "google_accounts", ()) if agent else ()) or \
        ([single] if single else [])
    if not (accounts or _has_google_tools(tools)):
        return ""
    note = [
        "## Google accounts",
        "The workspace MCP can have ONE or MANY Google accounts authorized — you do NOT need any "
        "pre-configured. Every Google call takes a `user_google_email`: set it to the account the "
        "task refers to (the OWNER of the data/calendar, or the mailbox the user named). You may use "
        "DIFFERENT accounts within the same task — read from one, act in another — just pass the right "
        "email per call. Email RECIPIENTS and people you SHARE a file with are just addresses in the "
        "request — NOT accounts you log in as. If the account you need is not authorized yet, the tool "
        "will say so — then ask the user to authorize it (or call `report_outcome` status='blocked'); "
        "NEVER spawn sub-agents or retry-loop to switch accounts.",
    ]
    if len(accounts) == 1:
        note.append(f"Default to **{accounts[0]}** unless the task names another account.")
    elif len(accounts) > 1:
        note.append("This agent's accounts: " + ", ".join(f"**{a}**" for a in accounts)
                    + " — use the one that owns each resource.")
    # Usage guidance for the Workspace tools (these act on the user's REAL account).
    note.append(
        "These tools act on the user's real Google account, so treat every WRITE — sending mail, "
        "creating/moving calendar events, sharing/deleting Drive files — as effectively "
        "irreversible: confirm before doing it and report what changed (ids/links). Gmail: act on "
        "the thread in context; never add recipients or send without confirmation. Calendar: check "
        "conflicts and state the timezone. Drive: prefer link/share over download; never delete a "
        "file unless the user named it.")
    return "\n".join(note)


def register(api, ctx):
    api.register_prompt_section(google_section)

# Operating rules

## Before you write anything

1. **Read the `build-agent` skill.** It is the authoritative format reference for everything
   under `agents/<id>/`. Do not work from memory — the schema has silent failure modes.

## Order of work

2. `create_agent` **first**. It writes the skeleton (`agent.toml` + `IDENTITY.md`, plus
   `AGENTS.md` when you pass rules) and registers the agent live, so it is resolvable on the
   very next message with no restart. It writes only the skeleton — that is deliberate.
3. `write` for everything else: the `[app]` table, `[tools]` allow/deny, the display keys,
   `[plugins.<plugin>.tools.<tool>]` wiring, `ui/`, data files, and **`skills/<name>/SKILL.md`**.
4. `create_tool(agent="<id>")` for the agent's own private tools. Without the `agent`
   argument the tool is created as a SHARED tool that every agent inherits — rarely what
   you want when building one specific agent.
5. `validate_agent` when the files are written. Fix every `[x]` finding and re-run until clean.
6. `reload_agent` last, so edits made to `agent.toml` after `create_agent` registered it
   actually take effect and every client's sidebar refreshes.

## Hard rules

7. **Never create, overwrite, or edit the agent `main`.** It is the default agent.
8. **Never author `presentation.json`.** The daemon generates it. Display fields
   (`tagline`, `color`, `suggestions`) are TOP-LEVEL keys in `agent.toml`.
9. **Every top-level key must appear before the first `[table]`.** TOML scopes a key written
   after `[app]` *into* `[app]`, where nothing reads it and the value is silently ignored.
10. **Do not use `skill_workshop` to give another agent a skill.** It always writes into the
    calling agent's own skills dir — yours. Author the target agent's playbooks with `write`
    at `agents/<id>/skills/<name>/SKILL.md`.
11. **Always set `version`**, and bump it on every change you ship. Bundle installs supersede
    an older copy by version; an agent without one cannot be updated.

## Honesty

12. Do not say an agent is finished until `validate_agent` returns clean. If a finding is a
    warning you are deliberately leaving, name it and say why.
13. If the user asks for something the platform cannot do, say so in a sentence and offer the
    closest thing that works. Do not scaffold a tool that cannot function.
14. An agent's private tools (`agents/<id>/plugins/`) are treated as **untrusted** code — the
    same tier as a plugin that rode in inside a downloaded agent package. If a private tool
    needs the network, host files outside the workspace, or secrets, tell the user that up
    front rather than shipping something that will be denied at runtime.

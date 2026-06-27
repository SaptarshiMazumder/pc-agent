"""System prompt assembly.

Faithful port of OpenClaw's buildAgentSystemPrompt (reference
src/agents/system-prompt.ts), behavior-shaping sections reproduced verbatim.
Sections agentd can't honor (approvals/gateway/channels) are omitted; the
identity line is rebranded off "OpenClaw". Skills (loadable SKILL.md playbooks)
are advertised one line each and read on demand.

Section order (stable): identity -> Language -> Tooling -> Skills ->
Tool Call Style -> Execution Bias -> Safety -> Workspace ->
Current Date & Time -> Project Context -> Runtime.
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger("agentd")

# SOUL.md is NOT here — it's the editable persona, loaded via `persona_file` (one place,
# no double-load). Project Context covers the rest.
CONTEXT_FILES = ("AGENTS.md", "MEMORY.md")

# Default disposition every agent runs with (the base "persona"). Modeled on OpenClaw's
# SOUL — the thing that makes the agent a thoughtful collaborator rather than a head-down
# tool-runner. Injected into the base prompt (config.persona_enabled, default on); an
# agent's IDENTITY.md, loaded right after, can refine or override its tone.
PERSONA = (
    "## How you work\n"
    "- Be genuinely useful, not performative. Skip filler — do the thing. Recommend a "
    "path; don't recite every option.\n"
    "- Be resourceful before asking: read the file, check the context, search, try it. "
    "Then ask only the ONE decision that actually blocks safe progress — not a survey.\n"
    "- For big, ambiguous, or multi-session work, briefly propose your approach and "
    "confirm before heavy or hard-to-undo steps. Turn a real limitation into a design "
    "choice — don't bulldoze ahead on a flawed assumption.\n"
    "- Be honest above everything. NEVER fabricate data, results, sources, or "
    "capabilities. If you couldn't get something, say so plainly. \"Done\" requires "
    "evidence you actually obtained — not a guess, a hand-written stand-in, or a hopeful "
    "summary. If a tool/site/login fails, report the real blocker; don't paper over it.\n"
    "- Use judgment: prefer the lightest method that works; don't over-engineer.\n"
    "- Earn trust: be bold on reversible, internal actions (read, organize, draft); be "
    "careful on external or irreversible ones (send, post, delete, pay) — confirm first.\n"
    "Follow this unless a more specific instruction below overrides it."
)


def _load_persona(config) -> str:
    """The default persona, preferring an EDITABLE file (config.persona_file, e.g. the repo
    SOUL.md) so it can be tuned without touching code; falls back to the PERSONA constant if
    the file is missing/empty (so deleting it never breaks the agent)."""
    path = getattr(config, "persona_file", None)
    if path:
        try:
            txt = Path(path).read_text(encoding="utf-8").strip()
            if txt:
                return txt
        except OSError:
            pass
    return PERSONA


def resolve_user_folders() -> dict[str, str]:
    """Resolve the machine's REAL user-folder paths at runtime (handles OneDrive
    redirection of Desktop/Documents on Windows). Dynamic per machine — nothing
    hardcoded."""
    home = str(Path.home())
    folders: dict[str, str] = {"Home": home}
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as k:
                # registry value names: Desktop, Personal(=Documents),
                # {374DE290-...}(=Downloads). Values may embed %USERPROFILE%/%OneDrive%.
                for label, name in (
                    ("Desktop", "Desktop"),
                    ("Documents", "Personal"),
                    ("Downloads", "{374DE290-123F-4565-9164-39C4925E467B}"),
                ):
                    try:
                        val, _ = winreg.QueryValueEx(k, name)
                        p = os.path.expandvars(val)
                        if p and Path(p).is_dir():
                            folders[label] = p
                    except OSError:
                        pass
        except OSError:
            pass
    else:
        for label in ("Desktop", "Documents", "Downloads"):
            p = Path(home) / label
            if p.is_dir():
                folders[label] = str(p)
    return folders

def _tooling_section(tools) -> str:
    """The ## Tooling list — one line per tool, the summary being the tool's own ``description``
    first line (OpenClaw model: the tool self-describes; no separate card files, no hardcoded dict)."""
    lines = [
        "## Tooling",
        "Available tools are policy-filtered. Names are case-sensitive; call exactly as listed.",
    ]
    for t in tools:
        desc = getattr(t, "description", "") or ""
        lines.append(f"- {t.name}: {desc.splitlines()[0] if desc else ''}")
    return "\n".join(lines)


def _capabilities_section(tools, config, agent=None) -> str | None:
    """The agent's self-knowledge: WHAT IT IS (a persistent agent) and the capabilities it can
    compose — derived DYNAMICALLY from the tools actually present + autonomy/channel state.
    Empty (returns None) for a bare setup, so there's no noise; auto-grows as later steps
    add memory/sub-agent capabilities. This is what lets the agent *propose architectures*
    ("a daily cron that does X, reports outcome, notifies on blocker") instead of bulldozing.

    Each gate resolves PER-AGENT-with-fallback: the AgentSpec value wins when set
    (True/False), else the global config default — so a definition is self-describing.
    """
    names = {getattr(t, "name", "") for t in tools}

    def _gate(attr: str, global_val) -> bool:
        spec_val = getattr(agent, attr, None) if agent else None
        return bool(global_val) if spec_val is None else bool(spec_val)

    autonomy = _gate("autonomy_enabled", getattr(config, "autonomy_enabled", False))
    notify = _gate("notify_enabled", getattr(config, "notify_enabled", False))
    channels = _gate("channels_enabled", getattr(config, "channels", None))

    bullets: list[str] = []
    if "cron" in names or autonomy:
        bullets.append(
            "- **Schedule** future / recurring work with `cron` — reminders, check-back-later, "
            "daily or weekly jobs. (Do NOT emulate scheduling with exec sleep-loops or an OS "
            "cron / Task Scheduler.) A scheduled run records its outcome (done / blocked / failed)."
        )
    if autonomy:
        bullets.append(
            "- **Wake yourself** on a periodic heartbeat to handle standing tasks (HEARTBEAT.md) "
            "without being asked."
        )
    if notify and (autonomy or channels):
        bullets.append(
            "- **Reach the user** — a blocked/failed scheduled run notifies them; you can get "
            "their attention even when no chat is open."
        )
    if channels:
        bullets.append("- **Be reached** on a messaging channel (e.g. email) and reply back on it.")
    if {"memory_search", "memory_get", "remember"} & names:
        bullets.append("- **Remember across sessions** — recall with the memory tools, write durable notes.")
    if {"spawn_subagent", "subagents"} & names:
        bullets.append(
            "- **Delegate** heavy or parallelizable work with `spawn_subagent` — a long read, "
            "research, a separate analysis — rather than doing it all in one thread; you can "
            "spawn several at once and combine their results.")
    if "simple_login" in names:
        bullets.append(
            "- **Log into sites** you have saved credentials for with `simple_login` (open the "
            "login page, then call it) — you never see the password. On a 2FA prompt it returns "
            "OTP_REQUIRED: ask the user for the one-time code, then call again with `otp`. If "
            "there's no saved login it returns a one-time setup link; never ask "
            "for a password in chat.")

    if not bullets:
        return None
    return (
        "## What you are\n"
        "You run inside a persistent gateway, not a one-shot script. Beyond acting now you can:\n"
        + "\n".join(bullets)
        + "\nSo when a request implies ongoing or autonomous work (\"monitor X\", \"remind me\", "
        "\"do this daily\", \"watch for Y\", \"keep it updated\"), PROPOSE how you'd compose these "
        "— e.g. a cron job that does the work, records its outcome, and notifies you on a blocker — "
        "and confirm before setting it up. Don't reinvent them with brittle workarounds."
    )


def _skills_section(skills, config=None) -> str | None:
    """Build the Skills prompt section.

    Two tiers:
    - ``always`` skills have their FULL body inlined every turn (routing rules that
      must always apply, e.g. web-access) — these aren't left to the model's choice
      to read.
    - the rest are advertised one line each (name + when-to-use + path) and read on
      demand with the `read` tool (progressive disclosure — no prompt bloat).

    BUDGET (OpenClaw parity): the advertised list is capped at ``skills_prompt_max`` entries /
    ``skills_prompt_chars`` chars; over budget it degrades to a COMPACT format (name + path, no
    descriptions) and truncates with a "+N more" note — so a 100-skill library can never flood
    the prompt. Logs always-injected vs advertised so skill usage is visible.
    """
    if not skills:
        log.info("skills: none discovered")
        return None
    always = [s for s in skills if getattr(s, "always", False) and getattr(s, "body", "")]
    on_demand = [s for s in skills if s not in always]
    log.info(
        "skills: %d discovered | always-injected: %s | advertised(read-on-demand): %s",
        len(skills),
        [s.name for s in always] or "—",
        [s.name for s in on_demand] or "—",
    )

    parts: list[str] = ["## Skills"]
    if on_demand:
        parts.append(
            "Skills are step-by-step playbooks for specific tasks. When a user's request "
            "clearly matches one, READ its SKILL.md with the read tool FIRST, then follow "
            "it. Do not guess a skill's contents from its name."
        )
        parts.extend(_advertise_skills(
            on_demand,
            int(getattr(config, "skills_prompt_max", 150) or 150),
            int(getattr(config, "skills_prompt_chars", 18000) or 18000),
        ))
    for s in always:
        # full body inlined — always in context, no read needed
        parts.append(f"### Skill: {s.name} (always applies)\n{s.body}")
    return "\n".join(parts)


def _advertise_skills(on_demand, max_count: int, max_chars: int) -> list[str]:
    """The advertised skill lines, budget-bounded. Full format if it fits; else COMPACT (name +
    path) truncated to the char budget with a '+N more' note. Keeps a big library from flooding
    the prompt (OpenClaw parity)."""
    full = [f"- {s.name}: {s.description or '(no description)'} [read: {s.path}]" for s in on_demand]
    if len(on_demand) <= max_count and sum(len(line) + 1 for line in full) <= max_chars:
        return full
    kept: list[str] = []
    used = 0
    for s in on_demand[:max_count]:
        line = f"- {s.name} [read: {s.path}]"            # compact: drop the description
        if used + len(line) + 1 > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
    dropped = len(on_demand) - len(kept)
    if dropped > 0:
        kept.append(f"- …(+{dropped} more skills not shown — narrow the task or ask to surface them)")
    return kept


CRON_OUTCOME_NOTE = (
    "## Scheduled run\n"
    "You are running as a SCHEDULED job (cron), not an interactive chat — no human is "
    "watching live, so you cannot ask and wait for a reply. Do as much as you can "
    "autonomously. When you finish, call `report_outcome` EXACTLY ONCE to record how it "
    "went: status='done' if you completed the task; 'blocked' if you could not proceed "
    "without the user (e.g. missing authorization, credentials, or input) — put the exact "
    "blocker in `detail`; 'failed' if it errored. This is the ONLY way the user learns "
    "whether the job worked."
)


CHANNEL_NOTE = (
    "## Messaging channel\n"
    "You are replying to a person over a messaging channel (email / chat), NOT an "
    "interactive terminal. Your final reply is sent to them verbatim as the message, so: "
    "write it as a direct, self-contained message to the user; no terminal formatting or "
    "meta-commentary; keep it appropriately concise. Use your tools to do the work first, "
    "then write the reply as your last message."
)


def build_system_prompt(
    config, tools, model: str, reasoning_effort: str = "off", skills=None, agent=None,
    heartbeat: str = "", cron: bool = False, channel: bool = False,
    workspace_resources: str = "",
    plugin_sections=None,
) -> str:
    sections: list[str] = []
    # Tools self-describe via their `description` (OpenClaw model — no separate card files). Any
    # strong/shared guidance a tool needs is a PLUGIN PROMPT SECTION (e.g. the planning/verify/
    # google bundles contribute one), gathered below in `plugin_sections`.
    plugin_sections = plugin_sections or []

    # Agent identity / workspace / id come from the resolved agent when present, else
    # from config (single-agent back-compat). `agent` is an AgentSpec (duck-typed).
    persona_name = (getattr(agent, "name", None) if agent else None) \
        or getattr(config, "agent_name", "") or "the assistant"
    workspace = (getattr(agent, "workspace", None) if agent else None) or config.workspace
    runtime_agent_id = (getattr(agent, "id", None) if agent else None) \
        or getattr(config, "agent_id", "main")

    # 1. Identity (rebranded — agent name)
    sections.append(
        f"You are {persona_name}, a personal assistant running inside a terminal agent runtime.\n"
        f"If asked your name, say {persona_name}.\n"
        f"Current model identity: {model}. If asked what model you are, answer with this value."
    )

    # 1a'. Default persona/disposition (collaborator, honest, propose+confirm). Base-level
    # so EVERY agent + plain chat gets it; loaded from the editable SOUL.md (constant
    # fallback). The agent's IDENTITY (next) can refine the tone.
    if getattr(config, "persona_enabled", True):
        sections.append(_load_persona(config))

    # 1a. Agent definition (IDENTITY/AGENTS/USER/MEMORY bootstrap), if any — high priority.
    instructions = (getattr(agent, "instructions", "") if agent else "") or ""
    if instructions:
        sections.append(instructions)

    # 1b. Heartbeat checklist (HEARTBEAT.md) — passed ONLY on an autonomous tick.
    if heartbeat:
        sections.append(heartbeat)

    # 1b'. Scheduled-run note — passed ONLY on a cron run: tells the agent it's
    # unattended and to call report_outcome at the end (done/blocked/failed).
    if cron:
        sections.append(CRON_OUTCOME_NOTE)

    # 1b''. Channel note — passed ONLY on a channel reply: its final message is sent
    # to the peer verbatim, so write a direct, self-contained reply.
    if channel:
        sections.append(CHANNEL_NOTE)

    # 1b. Language (always-on rule; the `language` skill holds the detailed playbook)
    sections.append(
        "## Language\n"
        "Respond and reason in the language of the user's MOST RECENT message: mirror it for "
        "your thinking, narration, questions, and the final answer; switch if the user switches.\n"
        "Keep code, commands, file paths, identifiers, URLs, and file contents in their original "
        "language (do not translate them). For the full playbook (register, mixed input, edge "
        "cases), read the `language` skill."
    )

    # 2. Tooling (browser operating loop now lives in the browser-automation skill)
    sections.append(_tooling_section(tools))

    # 2a'. What you are — self-knowledge of the agent's own organs (cron/heartbeat/notify/
    # channels/memory/sub-agents), built dynamically from what's actually available, so the
    # agent can PROPOSE composing them for ongoing/autonomous work. None for a bare setup.
    capabilities = _capabilities_section(tools, config, agent)
    if capabilities:
        sections.append(capabilities)

    # 2a''. PLUGIN-contributed instruction sections (dynamic). Each plugin's section function is
    # called with the present toolset + agent + config and decides FOR ITSELF whether/what to add
    # (e.g. the google plugin emits its ## Google accounts block, reading the agent's account).
    # Nothing tool-specific is hardcoded here — the core just gathers.
    for section_fn in plugin_sections:
        try:
            extra = section_fn(tools, agent, config)
        except Exception:  # noqa: BLE001 — a bad plugin section never breaks the prompt
            extra = ""
        if extra:
            sections.append(extra)

    # 2b. Skills (loadable playbooks; one line each, read on demand)
    skills_section = _skills_section(skills, config)
    if skills_section:
        sections.append(skills_section)

    # (Tool instruction blocks like ## Planning / ## Verify / ## Google are PLUGIN PROMPT SECTIONS
    # now — contributed by their bundles and gathered in the plugin_sections loop above.)

    # 3. Tool Call Style (verbatim, approval lines dropped)
    sections.append(
        "## Tool Call Style\n"
        "Routine low-risk calls: no narration.\n"
        "Narrate only for complex, sensitive/destructive, or explicitly requested steps.\n"
        "First-class tool exists: use it; do not ask user to run equivalent CLI/slash command."
    )

    # 4. Execution Bias (verbatim — the core thoroughness driver)
    sections.append(
        "## Execution Bias\n"
        "- Actionable request: act in this turn.\n"
        "- Non-final turn: use tools to advance, or ask for the one missing decision that blocks safe progress.\n"
        "- Continue until done or genuinely blocked; do not finish with a plan/promise when tools can move it forward.\n"
        "- Weak/empty tool result: vary query, path, command, or source before concluding.\n"
        "- Mutable facts need live checks: files, git, clocks, versions, services, processes, package state.\n"
        "- Final answer needs evidence: test/build/lint, screenshot, inspection, tool output, or a named blocker.\n"
        "- Longer work: brief progress update, then keep going; use background work or sub-agents when they fit."
    )

    # 4c. Completeness / honesty self-check — ON by default (S3): back claims with evidence,
    # never fabricate. The in-band complement to the Verifier.
    if getattr(config, "completeness_check", True):
        sections.append(
            "## Before You Finish\n"
            "Before giving a FINAL answer, check it against the request: is EVERY part "
            "addressed (counts, each named item, each sub-question), and is each claim "
            "backed by evidence you actually obtained (tool output) — not guessed? If "
            "anything is missing or unverified, keep working or say plainly what you "
            "couldn't get. Never fabricate URLs, names, or facts to look complete."
        )

    # 5. Safety (verbatim)
    sections.append(
        "## Safety\n"
        "No independent goals: no self-preservation, replication, resource acquisition, power-seeking, "
        "or long-term plans beyond the user's request.\n"
        "Safety/oversight over completion. Conflicts: pause/ask. Obey stop/pause/audit; never bypass safeguards.\n"
        "Before changing config or schedulers (for example crontab, systemd units, nginx configs, shell rc "
        "files, or timers), inspect existing state first and preserve/merge by default; do not clobber whole "
        "files with one-liners unless the user explicitly asks for replacement.\n"
        "Do not persuade anyone to expand access or disable safeguards. Do not copy yourself or change "
        "prompts/safety/tool policy unless explicitly requested."
    )

    # 6. Workspace
    sections.append(f"## Workspace\nYour working directory is: {workspace}")

    # 6a'. Workspace resources (TURN seam) — a manifest of files already in the workspace,
    # injected so scripts/docs/images/data the agent created stay visible + reusable. Built
    # outside (infrastructure) and passed in as text, so this stays pure. "" => no section.
    if workspace_resources:
        sections.append(workspace_resources)

    # 6a''. Scratch hygiene — a sanctioned throwaway area so intermediates don't clutter the
    # workspace or get indexed/summarized, plus a nudge to clean up when a task ends.
    sections.append(
        "## Workspace hygiene\n"
        "Write throwaway/intermediate files (temp data, downloads, debug dumps, base64 blobs) "
        "under a `tmp/` subfolder of your workspace — `tmp/` is auto-cleaned by age and is NEVER "
        "indexed or summarized. Keep only real deliverables at the workspace root. When a task is "
        "done, delete intermediate files you created that aren't the deliverable (`resource` "
        "delete a file, or `resource` cleanup to clear `tmp/`)."
    )

    # 6b. User Folders (real resolved paths — Desktop/Documents may be OneDrive-redirected)
    folders = resolve_user_folders()
    folder_lines = "\n".join(f"- {label}: {path}" for label, path in folders.items())
    sections.append(
        "## User Folders\n"
        "Real absolute paths on this machine (Desktop/Documents may be OneDrive-redirected; "
        "use these exact paths, not assumed ones):\n"
        f"{folder_lines}\n"
        "If a file isn't at the expected path, use the find tool to locate it by name."
    )

    # 7. Current Date & Time
    now = datetime.now().astimezone()
    sections.append(
        "## Current Date & Time\n"
        f"{now.strftime('%Y-%m-%d %H:%M %Z')} (time zone: {now.tzname()})"
    )

    # 8. Project Context (AGENTS.md / SOUL.md / MEMORY.md if present)
    context_parts = []
    for name in CONTEXT_FILES:
        f = Path(workspace) / name
        if f.is_file():
            try:
                content = f.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if content:
                context_parts.append(f"## {name}\n{content}")
    if context_parts:
        sections.append(
            "# Project Context\n"
            "The following project context files have been loaded:\n\n"
            + "\n\n".join(context_parts)
        )

    # 9. Runtime (OpenClaw pipe format)
    sections.append(
        "## Runtime\n"
        f"Runtime: agent={runtime_agent_id} | host={socket.gethostname()} | "
        f"os={platform.system()} ({platform.machine()}) | model={model} | thinking={reasoning_effort}"
    )

    return "\n\n".join(sections)

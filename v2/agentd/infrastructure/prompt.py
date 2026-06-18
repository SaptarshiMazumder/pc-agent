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

import os
import platform
import socket
import sys
from datetime import datetime
from pathlib import Path

CONTEXT_FILES = ("AGENTS.md", "SOUL.md", "MEMORY.md")


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

# Verbatim per-tool summary strings from system-prompt.ts (lines 742-779).
TOOL_SUMMARIES = {
    "update_plan": "Track short work plan",
    "read": "Read file contents",
    "write": "Create or overwrite files",
    "edit": "Make precise edits to files",
    "ls": "List directory contents",
    "find": "Find files by name/glob anywhere (use to locate a file instead of guessing its path)",
    "exec": "Run shell commands (pty available for TTY-required CLIs)",
    "process": "Manage background exec sessions",
    "web_search": "Search the web using the configured provider",
    "web_fetch": "Fetch and extract readable content from a URL",
    "browser": "Control web browser",
}


def _tooling_section(tools) -> str:
    lines = [
        "## Tooling",
        "Available tools are policy-filtered. Names are case-sensitive; call exactly as listed.",
    ]
    for t in tools:
        summary = TOOL_SUMMARIES.get(t.name) or (t.description.splitlines()[0] if t.description else "")
        lines.append(f"- {t.name}: {summary}")
    return "\n".join(lines)


def _skills_section(skills) -> str | None:
    """Advertise available skills as one line each (name + when-to-use + path).

    Only the descriptions live in the prompt; the agent reads the full SKILL.md on
    demand with the `read` tool. This is progressive disclosure — adding skills
    grows know-how without bloating the prompt.
    """
    if not skills:
        return None
    lines = [
        "## Skills",
        "Skills are step-by-step playbooks for specific tasks. When a user's request "
        "clearly matches one, READ its SKILL.md with the read tool FIRST, then follow "
        "it. Do not guess a skill's contents from its name.",
    ]
    for s in skills:
        desc = s.description or "(no description)"
        lines.append(f"- {s.name}: {desc} [read: {s.path}]")
    return "\n".join(lines)


def build_system_prompt(
    config, tools, model: str, reasoning_effort: str = "off", skills=None
) -> str:
    sections: list[str] = []

    # 1. Identity (rebranded — agent name from config)
    agent_name = getattr(config, "agent_name", "") or "the assistant"
    sections.append(
        f"You are {agent_name}, a personal assistant running inside a terminal agent runtime.\n"
        f"If asked your name, say {agent_name}.\n"
        f"Current model identity: {model}. If asked what model you are, answer with this value."
    )

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

    # 2b. Skills (loadable playbooks; one line each, read on demand)
    skills_section = _skills_section(skills)
    if skills_section:
        sections.append(skills_section)

    # 2c. Planning — a STRONG nudge (Gemini doesn't self-plan from the tool
    # description alone the way GPT-5 does, which is all OpenClaw relies on).
    if any(getattr(t, "name", "") == "update_plan" for t in tools):
        sections.append(
            "## Planning\n"
            "For ANY task that takes more than one step, your FIRST action MUST be to call "
            "`update_plan` — do NOT start the work before you have a plan. BREAK THE TASK "
            "DOWN into the smallest concrete steps, and for EACH step name the specific "
            "tool it uses. Trigger planning whenever the task: needs more than one tool, "
            "has more than ~2 steps, processes MULTIPLE items (several people / files / "
            "links), or reads as 'do X, then Y, then Z'. Keep the plan current with "
            "`update_plan` as you go (mark steps in_progress / completed), and use the BEST "
            "tool per step — `browser` (it is SIGNED IN via a persistent profile) or "
            "`web_search` for the web; `computer` ONLY when a task truly needs the real "
            "desktop GUI. ONLY skip planning for a genuinely simple, single-step request.\n"
            "Example - \"check my unread LinkedIn messages and who the recent senders are\":\n"
            "  1. browser: open LinkedIn messaging; count unread; note the recent senders\n"
            "  2. web_search: for each sender, find their business + public profile link\n"
            "  3. reply: summarize the unread count + the people"
        )

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
    sections.append(f"## Workspace\nYour working directory is: {config.workspace}")

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
        f = Path(config.workspace) / name
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
        f"Runtime: agent={config.agent_id} | host={socket.gethostname()} | "
        f"os={platform.system()} ({platform.machine()}) | model={model} | thinking={reasoning_effort}"
    )

    return "\n\n".join(sections)

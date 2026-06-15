"""System prompt assembly.

Faithful port of OpenClaw's buildAgentSystemPrompt (reference
src/agents/system-prompt.ts), behavior-shaping sections reproduced verbatim.
Sections agentd can't honor (approvals/gateway/channels/skills) are omitted;
the identity line is rebranded off "OpenClaw".

Section order (stable): identity -> Tooling (+ browser usage block) ->
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

# Distilled verbatim from extensions/browser/skills/browser-automation/SKILL.md.
BROWSER_USAGE_BLOCK = """\
Browser operating loop (use for anything beyond a single page check):
- Read before you click: take action="snapshot" first; use a ref from the latest snapshot.
- Act narrowly: prefer action="act" with a ref. After navigation, modal changes, or form
  submission, snapshot again before the next action.
- Long or lazy-loaded lists (job boards, feeds, search results): the first snapshot only
  shows what is currently rendered. To gather more, act kind="scrollIntoView" on the last
  visible item (or kind="evaluate" fn="window.scrollBy(0, 2000)"), then act kind="wait"
  load_state="networkidle", then snapshot again. Repeat until you have enough items.
- Avoid blind waits: wait for visible UI state (load_state="networkidle", text=..., selector=...).
- Use mode="efficient" snapshots to cut noise on large pages; raise the limit/depth when you
  need more of the tree.
- Report real blockers: if the page needs login, captcha, 2FA, or a permission dialog, stop and
  tell the user exactly what is needed. Do not claim you are not logged in just because a
  permission or onboarding dialog is showing; inspect the visible UI first."""


def _tooling_section(tools, has_browser: bool) -> str:
    lines = [
        "## Tooling",
        "Available tools are policy-filtered. Names are case-sensitive; call exactly as listed.",
    ]
    for t in tools:
        summary = TOOL_SUMMARIES.get(t.name) or (t.description.splitlines()[0] if t.description else "")
        lines.append(f"- {t.name}: {summary}")
    if has_browser:
        lines.append("")
        lines.append(BROWSER_USAGE_BLOCK)
    return "\n".join(lines)


def build_system_prompt(config, tools, model: str, reasoning_effort: str = "off") -> str:
    has_browser = any(t.name == "browser" for t in tools)
    sections: list[str] = []

    # 1. Identity (rebranded)
    sections.append(
        "You are a personal assistant running inside a terminal agent runtime.\n"
        f"Current model identity: {model}. If asked what model you are, answer with this value."
    )

    # 2. Tooling (+ browser usage block)
    sections.append(_tooling_section(tools, has_browser))

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

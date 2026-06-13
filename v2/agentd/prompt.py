"""System prompt assembly.

Minimal port of the reference system prompt builder. Fixed section order:
identity -> Tooling -> Tool Call Style -> Execution Bias -> Workspace ->
Current Date & Time -> Project Context (AGENTS.md / SOUL.md / MEMORY.md) ->
Runtime.
"""

from __future__ import annotations

import platform
import socket
from datetime import datetime
from pathlib import Path

CONTEXT_FILES = ("AGENTS.md", "SOUL.md", "MEMORY.md")


def build_system_prompt(config, tools, model: str) -> str:
    sections: list[str] = []

    sections.append(
        "You are a personal assistant running inside a terminal agent runtime.\n"
        f"Current model identity: {model}. If asked what model you are, answer with this value."
    )

    tool_lines = "\n".join(
        f"- {t.name}: {t.description.splitlines()[0]}" for t in tools
    )
    sections.append(
        "## Tooling\n"
        "Available tools are listed below. Names are case-sensitive; call exactly as listed.\n"
        f"{tool_lines}"
    )

    sections.append(
        "## Tool Call Style\n"
        "Routine low-risk calls: no narration.\n"
        "Narrate only for complex or explicitly requested steps.\n"
        "If a first-class tool exists for a task, use it instead of asking the user to run a command."
    )

    sections.append(
        "## Execution Bias\n"
        "- Actionable request: act in this turn.\n"
        "- Use tools to advance the task, or ask for the one missing decision that blocks progress.\n"
        "- Continue until done or genuinely blocked; avoid stopping mid-task to ask for confirmation."
    )

    sections.append(f"## Workspace\nYour working directory is: {config.workspace}")

    now = datetime.now().astimezone()
    sections.append(
        "## Current Date & Time\n"
        f"{now.strftime('%Y-%m-%d %H:%M %Z')} (time zone: {now.tzname()})"
    )

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

    sections.append(
        "## Runtime\n"
        f"Runtime: agent={config.agent_id} | host={socket.gethostname()} | "
        f"os={platform.system()} ({platform.machine()}) | model={model}"
    )

    return "\n\n".join(sections)

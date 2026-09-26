"""App context — notes an agent's own APP sends into the chat for the agent, not from the person.

Agent Builder's window opens every chat about an existing agent with one
(agents/agent-builder/app/src/agentd/chat.ts: "[context] We are working on the EXISTING agent…").
It rides as a user message because that is how the model receives it, but it is the app telling
the agent WHERE the work happens. Read as the person's words it became a requirement — "inspect
and summarise the current setup" — and the user was shown a tour of an empty starter template for
approval. The project manager reads these as context, never as requirements.

The prefix is a contract with the app code named above; change both together.
"""

from __future__ import annotations

APP_CONTEXT_PREFIX = "[context]"


def is_app_context(text: str) -> bool:
    return (text or "").lstrip().startswith(APP_CONTEXT_PREFIX)


__all__ = ["APP_CONTEXT_PREFIX", "is_app_context"]

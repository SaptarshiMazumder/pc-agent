"""AgentService — the use-case for handling one inbound user message.

This is the APPLICATION layer: pure orchestration, no IO libraries. It coordinates
the steps of a turn by calling interfaces only — everything concrete (the engine,
the tools, how to make a session store, how to build the system prompt) is INJECTED
in the constructor. So this class imports nothing from infrastructure and never
changes when you swap the engine, the memory backend, or the model.

The conductor analogy: this class decides "load history, append the user message,
build the prompt, run the engine, persist" — the *order of the work* — but plays no
instrument itself (the engine streams the LLM, the session store hits the disk).
"""

from __future__ import annotations

from typing import Callable

from agentd.application.interfaces.agent_engine import AgentEngine
from agentd.application.interfaces.events import EventSink
from agentd.application.interfaces.memory import SessionStore
from agentd.domain.messages import UserMessage


class AgentService:
    def __init__(
        self,
        *,
        engine: AgentEngine,
        tools: list,
        make_session: Callable[[str], SessionStore],  # session_id -> a SessionStore
        build_prompt: Callable[[list], str],          # tools -> the system prompt text
    ):
        self._engine = engine
        self._tools = tools
        self._make_session = make_session
        self._build_prompt = build_prompt

    async def handle_message(self, session_id: str, text: str,
                             on_event: EventSink, abort) -> None:
        """Run one turn end to end for the given session."""
        session = self._make_session(session_id)         # the memory store for this session
        messages = session.load()                         # prior history (read)
        user_msg = UserMessage(content=text)
        messages.append(user_msg)                         # add the new user turn to context
        session.append(user_msg)                          # persist it
        system_prompt = self._build_prompt(self._tools)   # identity + tool list + context
        # hand off to the engine; it streams the LLM, runs tools, and re-feeds until done.
        # (it persists each assistant/tool message via the `session` it's given.)
        await self._engine.run(
            messages=messages,
            system_prompt=system_prompt,
            tools=self._tools,
            on_event=on_event,
            abort=abort,
            session=session,
        )

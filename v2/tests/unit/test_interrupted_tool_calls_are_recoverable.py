"""An interrupted run must not destroy a conversation.

THE FAILURE THIS PINS. The loop persists the assistant turn, runs the tools, then persists their
results. Stop the process in between — a daemon restart, a crash, a killed task — and the call is
on disk with no result. Providers validate the whole message array before running anything, so
every later send is rejected with a 400 naming the unanswered `tool_call_id`, and the transcript
can never recover on its own.

Seen in the wild before this was fixed: a session with 97 healthy records answered eleven
consecutive messages with "Agent couldn't generate a response" and stayed that way permanently.
"""

from __future__ import annotations

from agent_runtime.domain.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from agent_runtime.infrastructure.memory.local_store import (
    SessionStore,
    close_unanswered_tool_calls,
)


def _call(call_id: str, name: str = "exec") -> AssistantMessage:
    return AssistantMessage(content=[ToolCallContent(id=call_id, name=name, arguments={})])


def test_an_unanswered_tool_call_gets_a_result():
    """The invariant every provider enforces: one tool message per tool_call_id."""
    repaired = close_unanswered_tool_calls([UserMessage(content="build it"), _call("call_1")])

    results = [m for m in repaired if isinstance(m, ToolResultMessage)]
    assert [r.tool_call_id for r in results] == ["call_1"]
    # It reports the truth — interrupted, not "succeeded". An invented success would have the
    # model build on work that never happened.
    assert results[0].is_error
    assert "did not finish" in results[0].text


def test_the_synthetic_result_goes_directly_after_its_call():
    """Position is part of the contract, not a detail: the tool message must FOLLOW the assistant
    message that made the call, or the request is rejected exactly as before."""
    repaired = close_unanswered_tool_calls(
        [UserMessage(content="go"), _call("call_1"), UserMessage(content="continue")]
    )

    kinds = [type(m).__name__ for m in repaired]
    assert kinds == ["UserMessage", "AssistantMessage", "ToolResultMessage", "UserMessage"]


def test_a_call_that_already_has_its_result_is_left_alone():
    """The healthy path is the common one. Duplicating a result would break a working
    conversation to fix a broken one."""
    answered = [
        _call("call_1"),
        ToolResultMessage(tool_call_id="call_1", tool_name="exec", content=[TextContent(text="ok")]),
    ]
    repaired = close_unanswered_tool_calls(list(answered))

    assert repaired == answered


def test_every_call_of_a_multi_call_turn_is_answered():
    """A turn may fan out several tools at once, and the provider wants a message for EACH id —
    answering only the first leaves the request just as malformed."""
    turn = AssistantMessage(
        content=[
            ToolCallContent(id="a", name="read", arguments={}),
            ToolCallContent(id="b", name="ls", arguments={}),
        ]
    )
    repaired = close_unanswered_tool_calls([turn])

    assert [m.tool_call_id for m in repaired if isinstance(m, ToolResultMessage)] == ["a", "b"]


def test_loading_a_broken_transcript_repairs_it(tmp_path):
    """End to end, through the store the run actually uses — and WITHOUT rewriting the file. The
    transcript is the record of what happened, and an interrupted call is what happened; the
    repair belongs in the replay, so every already-broken session heals when next opened."""
    store = SessionStore(tmp_path, "chat-broken")
    store.load()  # creates the header
    store.append(UserMessage(content="build a workflow"))
    store.append(_call("call_1"))
    store.append(UserMessage(content="continue"))  # the run died before the result was written

    before = store.path.read_text(encoding="utf-8")
    loaded = SessionStore(tmp_path, "chat-broken").load()

    assert [m.tool_call_id for m in loaded if isinstance(m, ToolResultMessage)] == ["call_1"]
    assert store.path.read_text(encoding="utf-8") == before, "the transcript must not be rewritten"

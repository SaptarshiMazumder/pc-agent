"""S7 — WindowContextPolicy: boundary-safe truncation of the model's view of history."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.domain.messages import AssistantMessage, TextContent, UserMessage
from agentd.infrastructure.context import WindowContextPolicy


def _a(t):
    return AssistantMessage(content=[TextContent(text=t)])


def test_window_returns_all_under_budget():
    msgs = [UserMessage(content="hi"), _a("yo")]
    assert WindowContextPolicy(10).prepare(msgs) is msgs  # untouched


def test_window_keeps_recent_from_a_user_boundary():
    msgs = [
        UserMessage(content="u1"),
        _a("a1"),
        UserMessage(content="u2"),
        _a("a2"),
        UserMessage(content="u3"),
        _a("a3"),
    ]
    out = WindowContextPolicy(3).prepare(msgs)
    # last 3 = [a2, u3, a3]; must not start mid-turn -> advance to u3
    assert [m.role for m in out] == ["user", "assistant"]
    assert out[0].content == "u3"

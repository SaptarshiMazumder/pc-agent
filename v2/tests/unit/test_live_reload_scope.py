"""Who hears `app.rebuilt` — and the case the policy does NOT cover.

`_scoped_event_allowed` is the whole scoping rule for an agent-SCOPED app connection, which is why
the shared `LiveReload` component deliberately does not re-check the id: the daemon already did.

A HOST connection is the exception. It bypasses that policy entirely — it has to, because Agent
Builder's window needs to see every agent in order to build them — so for a host window there is
no daemon-side filter at all. Wiring the component there without an id would reload the builder
whenever ANY agent was rebuilt, in the middle of the conversation building it.

These pin both halves, because the two are easy to conflate and the failure only shows up as "my
window keeps refreshing while I work".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.presentation.gateway import _scoped_event_allowed

V2 = Path(__file__).resolve().parents[2]
TPL = V2 / "agents/agent-builder/skills/build-agent/templates"


def test_a_scoped_window_hears_only_its_own_rebuild():
    assert _scoped_event_allowed("app.rebuilt", {"agentId": "mine"}, "mine") is True
    assert _scoped_event_allowed("app.rebuilt", {"agentId": "someone-else"}, "mine") is False


def test_a_rebuild_with_no_agent_id_reaches_nobody():
    """Fails closed. A payload that names no agent cannot be shown to be about yours."""
    assert _scoped_event_allowed("app.rebuilt", {}, "mine") is False


def test_the_component_filters_only_when_given_an_id():
    """The contract the two callers rely on: an agent's own window passes no id and trusts the
    daemon; a host window passes one because the daemon did not filter for it."""
    body = (TPL / "_common/dev/LiveReload.tsx").read_text(encoding="utf-8")
    assert "agentId?: string" in body
    assert "if (agentId && payload?.agentId !== agentId) return" in body


def test_the_skeleton_passes_no_id_and_agent_builder_passes_one():
    """THE PAIR. A scaffolded agent's window is agent-scoped, so re-checking would duplicate the
    daemon's rule. Agent Builder's is host-scoped, so NOT checking would reload it on every
    agent's rebuild — the exact failure this file exists to describe."""
    skeleton = (TPL / "_skeleton/src/App.tsx").read_text(encoding="utf-8")
    assert "<LiveReload client={client ?? undefined} />" in skeleton

    builder = (V2 / "agents/agent-builder/app/src/App.tsx").read_text(encoding="utf-8")
    assert "agentId={AGENT_ID}" in builder
    assert "<LiveReload" in builder

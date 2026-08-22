"""THE SETTINGS PAGE IS THE SAME PAGE EVERYWHERE — and this is what keeps that true.

An agent's settings window and the assistant's own are two implementations of one screen. They
cannot be one file: the assistant's renderer ships compiled and its source never reaches a user's
machine, while an agent's source is packaged and installed. So the common module carries a COPY.

A copy drifts. Someone adds a knob to the assistant's schema, the common one does not get it, and
every agent quietly ships a smaller page than the product it lives in. Nothing would notice —
which is why this test exists rather than a note asking people to remember.

It compares the two FIELD LISTS, not the markup. Layout is allowed to differ (an agent's page has
a layer the assistant's does not); the set of knobs a user can reach is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

V2 = Path(__file__).resolve().parents[2]
AGENTD_SCHEMA = V2 / "clients" / "ui" / "src" / "lib" / "settingsSchema.ts"
COMMON_SCHEMA = (
    V2
    / "agents"
    / "agent-builder"
    / "skills"
    / "build-agent"
    / "templates"
    / "_common"
    / "settings"
    / "schema.ts"
)

#: Knobs the assistant shows that an agent deliberately does not.
#:
#: `client:*` are renderer preferences of the assistant's own window (theme, its notification
#: setting) — they live in that window's localStorage and mean nothing inside an agent.
#: `env:*` are provider keys, which an agent's page renders from the daemon's `providerKeys`
#: list rather than from a hardcoded schema, so that a key added server-side appears without a
#: client release.
NOT_FOR_AGENTS = ("client:", "env:")


def _keys(path: Path) -> set[str]:
    """Every `key: '...'` in a schema file. Crude on purpose: a parser would have to understand
    two different TypeScript shapes, and the thing under test is the SET of knobs, not syntax."""
    return set(re.findall(r"key:\s*'([^']+)'", path.read_text(encoding="utf-8")))


def _agentd_keys() -> set[str]:
    return {k for k in _keys(AGENTD_SCHEMA) if not k.startswith(NOT_FOR_AGENTS)}


def test_both_schemas_are_where_this_test_expects():
    """A rename that moved either file would otherwise make this test pass by comparing two empty
    sets — the quiet kind of dead check."""
    assert AGENTD_SCHEMA.is_file(), AGENTD_SCHEMA
    assert COMMON_SCHEMA.is_file(), COMMON_SCHEMA
    assert len(_agentd_keys()) > 20, "the assistant's schema parsed to almost nothing"


def test_an_agent_reaches_every_knob_the_assistant_does():
    """The promise, stated as a set difference. A knob added to the assistant's page must be added
    to the common one, or agents ship a page that is missing it and nobody finds out."""
    missing = _agentd_keys() - _keys(COMMON_SCHEMA)
    assert not missing, (
        "these settings exist in the assistant's window but not in the common agent page: "
        f"{sorted(missing)}\n"
        f"Add them to {COMMON_SCHEMA.relative_to(V2).as_posix()} — every agent gets them at once."
    )


def test_the_agent_page_may_add_knobs_the_assistant_has_no_use_for():
    """Not symmetrical, deliberately. An agent's page carries per-agent concepts the assistant's
    has no layer for — `cost_efficiency.*` addressed inside an agent's own block, for instance. The
    rule is 'at least everything', not 'exactly the same list'."""
    extra = _keys(COMMON_SCHEMA) - _agentd_keys()
    # Asserted as a fact rather than allowed silently: if this ever empties, the two lists have
    # converged and the asymmetry above stopped being the reason this test is one-directional.
    assert extra, "the common schema adds nothing of its own — is it still the agent-layer page?"


@pytest.mark.parametrize(
    "key",
    [
        "model",
        "reasoning_effort",
        "max_turns",
        "cost_efficiency.enabled",
        "cost_efficiency.text_model",
        "cost_efficiency.vision_model",
    ],
)
def test_the_knobs_that_decide_which_model_runs_are_present(key):
    """Named individually because these six are the ones that went wrong. An agent set its model,
    cost efficiency overwrote it every turn, and the page showed no sign of which had won — so a
    page missing any of them is a page that cannot explain its own behaviour."""
    assert key in _keys(COMMON_SCHEMA)

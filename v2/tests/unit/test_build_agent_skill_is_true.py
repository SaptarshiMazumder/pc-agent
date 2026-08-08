"""The build-agent skill must not lie.

That skill is the ONLY description of the protocol the agent-building model ever reads. When
it disagrees with the runtime, the model writes code that cannot work — and the failure is
silent, because a UI listening for an event that never fires looks exactly like a UI whose
socket is down.

This happened. The skill listed `message_delta`, which has never existed (the real event is
`message_update` with `kind: "text_delta"`), and a generated inbox agent shipped with every
event branch dead. Separately, the skill said `config.get`/`config.set` were denied to apps;
that was true when written and became false when they were added to APP_SCOPED_METHODS, and
nothing noticed.

So the canonical lists live in TAGGED FENCES in the skill (```text agentd:events and
```text agentd:app-methods) and are checked against the runtime here. Drift is now a failing
test instead of a broken agent discovered weeks later.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.presentation.gateway import APP_SCOPED_METHODS

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "agents" / "agent-builder" / "skills" / "build-agent" / "SKILL.md"
RUNTIME = ROOT / "agent_runtime"

# Emitted by the engine but deliberately NOT part of the app contract: relayed sub-agent
# beats and the egress privacy gate are internal plumbing, not things an agent UI renders.
INTERNAL_EVENTS = frozenset({"subagent_event", "safe_to_send", "message_start"})

# Callable by an app connection, but deliberately NOT advertised to agent authors. These exist
# for the AGENT-PRODUCT SHELL — a standalone agent .exe acts as a first-party desktop client
# and signs the user in so the local daemon runs on platform keys. An ordinary agent UI has no
# business repointing the daemon's model proxy, and `setGatewayUrl` is a deprecated alias kept
# only for pre-rename clients. Listing them would invite exactly the wrong use.
UNADVERTISED_METHODS = frozenset({"platform.setModelProxyUrl", "platform.setGatewayUrl"})


def _fence(tag: str) -> list[str]:
    """The lines of a ```text <tag> fenced block in the skill."""
    text = SKILL.read_text(encoding="utf-8")
    m = re.search(rf"```text {re.escape(tag)}\n(.*?)```", text, re.S)
    assert m, f"the skill no longer has a '{tag}' block — the contract lists must stay tagged"
    return [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]


def documented_events() -> set[str]:
    return {ln.split()[0] for ln in _fence("agentd:events")}


def documented_methods() -> set[str]:
    return set(_fence("agentd:app-methods"))


def emitted_events() -> set[str]:
    """Every event name the runtime actually constructs."""
    names: set[str] = set()
    for py in RUNTIME.rglob("*.py"):
        names |= set(re.findall(r'AgentEvent\(\s*"([a-z_]+)"', py.read_text(encoding="utf-8")))
    # tool + observability events are emitted through the same sink with literal names
    for py in RUNTIME.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        names |= set(
            re.findall(
                r'"(tool_execution_start|tool_execution_end|tool_progress|model_trace|model_fallback)"',
                src,
            )
        )
    return names


# --- events -----------------------------------------------------------------
def test_every_documented_event_actually_exists():
    """The `message_delta` bug: a plausible-sounding name nobody emits. The model has no way
    to tell an invented event from a real one, so the skill must not contain any."""
    invented = documented_events() - emitted_events()
    assert not invented, (
        f"the skill documents events the runtime never emits: {sorted(invented)}. "
        f"A generated UI listening for these will silently do nothing."
    )


def test_every_user_facing_event_is_documented():
    """The other direction: a NEW event nobody wrote down is invisible to every agent built
    from now on. `model_fallback` was exactly this — it existed for a day before the skill
    mentioned it, so generated UIs could not have shown it."""
    undocumented = emitted_events() - documented_events() - INTERNAL_EVENTS
    assert not undocumented, (
        f"these events reach app connections but are not in the skill: {sorted(undocumented)}. "
        f"Document them, or add them to INTERNAL_EVENTS if apps should not see them."
    )


@pytest.mark.parametrize("name", ["message_update", "agent_end", "tool_execution_start"])
def test_the_events_a_ui_cannot_work_without(name):
    assert name in documented_events()


def test_message_delta_never_comes_back():
    """A regression pin on the specific fiction that broke a shipped agent."""
    assert "message_delta" not in documented_events()
    assert "message_delta" not in emitted_events()


# --- app-callable methods ---------------------------------------------------
def test_documented_methods_are_all_really_callable():
    wrong = documented_methods() - set(APP_SCOPED_METHODS)
    assert not wrong, f"the skill promises methods an app connection cannot call: {sorted(wrong)}"


def test_no_callable_method_is_left_undocumented():
    """Catches the `config.*` case in reverse: the runtime gained a capability and the skill
    kept telling the model it was forbidden."""
    missing = set(APP_SCOPED_METHODS) - documented_methods() - UNADVERTISED_METHODS
    assert not missing, (
        f"app connections can call these, but the skill omits them: {sorted(missing)}. "
        f"Document them, or add to UNADVERTISED_METHODS with a reason."
    )


def test_the_skill_no_longer_claims_config_is_denied():
    """It said 'config.get / config.set are denied. Do not build a settings screen.' That
    became false, and with it the model's ability to give a shipped agent a BYOK screen."""
    text = SKILL.read_text(encoding="utf-8")
    assert "are denied" not in text or "config.get` / `config.set` are denied" not in text
    assert "config.get" in documented_methods() or "config.get" in text


# --- the shape that actually broke the generated agent ----------------------
def test_the_skill_warns_that_the_payload_is_nested():
    """A generated UI read `payload.type` and every branch died. The nesting has to be stated
    where it cannot be missed, not left to be inferred from a type name."""
    text = SKILL.read_text(encoding="utf-8")
    assert "payload.event" in text or "NOT payload.type" in text
    assert "event: { type" in text or "event: {type" in text

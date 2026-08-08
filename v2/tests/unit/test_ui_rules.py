"""Reading the agent's own app code, so a dead UI is caught before it ships.

The trigger: a generated agent whose every event branch was unreachable. It passed validation
because nothing opened `ui/app.js`. Both defects were plainly visible in the file.

The hard part is NOT detection — it is not crying wolf. `.type` is the discriminator on stored
content blocks, on DOM nodes, on anything; flagging every `.type === '...'` fired on working
code, and a rule that cries wolf gets switched off. So the checks are scoped to identifiers
that genuinely hold an event, and stay silent otherwise.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_authoring.domain.ui_rules import UiRules

from agent_runtime.domain.events import APP_FACING_EVENTS, MESSAGE_UPDATE_KINDS
from agent_runtime.presentation.gateway import APP_SCOPED_METHODS

RULES = UiRules(
    events=APP_FACING_EVENTS,
    kinds=MESSAGE_UPDATE_KINDS,
    methods=frozenset(APP_SCOPED_METHODS),
    sdk_methods=frozenset(),
)


def check(js: str, path: str = "ui/app.js"):
    return RULES.check(None, {}, [path], {path: js})


def codes(js: str) -> set[str]:
    return {f.code for f in check(js)}


# --- the two defects that shipped -------------------------------------------
def test_reading_the_wrapper_as_the_event_is_an_error():
    """`onRun` hands you {sessionKey, runId, agentId, ts, event}. Switching on payload.type
    misses every branch, and the page just never updates."""
    js = """
    client.onRun(key, (payload) => {
      if (payload.type === 'tool_execution_start') start()
    })
    """
    assert "EVENT_PAYLOAD_NOT_NESTED" in codes(js)


def test_an_event_name_that_does_not_exist_is_an_error():
    js = """
    client.onRun(key, (payload) => {
      const ev = payload.event
      if (ev.type === 'message_delta') append(ev.delta)
    })
    """
    found = [f for f in check(js) if f.code == "UNKNOWN_EVENT"]
    assert found, "an invented event name must be caught"
    assert "message_update" in found[0].fix  # points at the real one


def test_a_named_handler_is_analysed_too():
    """Both hand-written agents in this repo pass the callback BY NAME. Matching only inline
    arrows silently passed over a genuine dead branch in both of them."""
    js = """
    function onEvent(payload) {
      const e = payload.event || {}
      switch (e.type) {
        case 'message_delta': append(e.delta); break
      }
    }
    client.onRun(session, onEvent)
    """
    assert "UNKNOWN_EVENT" in codes(js)


# --- silence on code that works ---------------------------------------------
def test_a_correct_handler_is_clean():
    js = """
    client.onRun(key, (payload) => {
      const ev = payload.event
      switch (ev.type) {
        case 'message_update':
          if (ev.kind === 'text_delta') append(ev.delta)
          else if (ev.kind === 'thinking_delta') think(ev.delta)
          break
        case 'tool_execution_start': startRow(ev.toolName); break
        case 'tool_execution_end':   endRow(ev.isError); break
        case 'model_fallback':       warn(ev.from, ev.to); break
        case 'agent_end':            done(ev.stopReason); break
      }
    })
    """
    assert not check(js)


def test_content_block_types_are_not_mistaken_for_events():
    """`c.type === 'tool_use'` is a stored CONTENT BLOCK, not an event. This exact shape is in
    agent-builder's own chat.js and produced a false alarm before the check was scoped."""
    js = """
    function replay(m) {
      for (const c of m.content) {
        if (c.type !== 'tool_use' && c.type !== 'toolcall') continue
        row(c.name)
      }
    }
    """
    assert not check(js)


def test_dom_and_other_type_fields_are_ignored():
    js = """
    input.type = 'password'
    if (entry.kind === 'folder') expand(entry)
    if (node.type === 'submit_button') go()
    """
    assert not check(js)


def test_the_vendored_sdk_is_never_analysed():
    """It is copied verbatim and is not the agent's code."""
    js = "if (frame.type === 'res') { pending.resolve() }"
    assert not RULES.check(None, {}, [], {"ui/vendor/agentd-client.js": js})


# --- calling things an app may not call -------------------------------------
def test_a_host_only_rpc_is_an_error():
    js = "await client.request('marketplace.install', { id: 'x' })"
    found = [f for f in check(js) if f.code == "METHOD_NOT_APP_CALLABLE"]
    assert found and "marketplace.install" in found[0].message


def test_an_app_tier_rpc_is_fine():
    js = """
    await client.request('workspace.list', { agentId: 'x' })
    await client.request('config.get')
    """
    assert not check(js)

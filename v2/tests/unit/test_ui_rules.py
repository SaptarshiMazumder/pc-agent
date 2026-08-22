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

from agent_authoring.domain.ui_component import UiComponents
from agent_authoring.domain.ui_rules import UiRules

from agent_runtime.domain.events import APP_FACING_EVENTS, MESSAGE_UPDATE_KINDS
from agent_runtime.presentation.gateway import APP_SCOPED_METHODS

RULES = UiRules(
    events=APP_FACING_EVENTS,
    kinds=MESSAGE_UPDATE_KINDS,
    methods=frozenset(APP_SCOPED_METHODS),
    sdk_methods=frozenset(),
    # The REAL catalogue, exactly as the composition root passes it. Injecting it here rather than
    # hand-writing the patterns is the whole point of the refactor these tests cover: if a
    # component's `detect` changes, this suite follows it instead of asserting a stale copy.
    components=UiComponents().all(),
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


# --- hosted sign-in ---------------------------------------------------------
# An app agent with no login works fine on the author's machine and is UNUSABLE on a hosted
# install: every model call fails with a provider error and nothing on screen explains why. That
# shipped, and the only reason it was found is that someone tried to sign in and could not.
#
# The scaffolded template already awaits the gate, so this rule exists for the agents that did not
# come from it — and, as always here, for staying quiet on the ones that are fine.

APP = {"app": {"width": 1100}}
SDK = "ui/vendor/agentd-client.js"


def sign_in(js: str, vendored: str = "function mountSignInGate(){}"):
    sources = {"ui/app.js": js}
    if vendored is not None:
        sources[SDK] = vendored
    return {f.code: f for f in RULES.check(None, APP, list(sources), sources)}


def test_an_app_agent_with_no_sign_in_is_refused():
    """MANDATORY, not advisory. It was a warning for exactly as long as it took to publish past
    one — an agent with a window has to know who is using it, and on a hosted install every model
    call fails without it, with nothing on screen to explain why. The rulebook also closes PACK
    and PUBLISH on this code, so an agent that skips it cannot ship at all."""
    found = sign_in("const client = agentd.fromPage()")
    assert "UI_NO_SIGN_IN" in found
    assert found["UI_NO_SIGN_IN"].level == "error"
    # The fix names the TOOL, not the line to type. Ranked by strength: a tool that does every
    # step (SDK refresh, script tag, theme tokens, the call) and is safe to re-run beats an
    # instruction to hand-write one of the four and forget the rest — which is how an agent ends
    # up calling a gate its year-old vendored SDK cannot run.
    assert "add_ui_component" in found["UI_NO_SIGN_IN"].fix
    assert "sign-in" in found["UI_NO_SIGN_IN"].fix


def test_calling_the_gate_is_clean():
    assert "UI_NO_SIGN_IN" not in sign_in("await agentd.mountSignInGate()")


def test_using_the_mechanism_directly_is_also_clean():
    """figure-creator drives its own sign-in surface with resolveAuth/signIn instead of the modal.
    That is a legitimate choice, and forcing the drop-in gate on it would be the rule dictating
    design rather than catching a defect."""
    js = """
    const auth = await agentd.resolveAuth()
    if (auth.needsSignIn) await agentd.signIn({ email, password })
    """
    assert "UI_NO_SIGN_IN" not in sign_in(js)


def test_a_component_whose_sdk_symbol_is_missing_is_an_error():
    """The drift case: app.js was updated, ui/vendor/agentd-client.js was not. Guaranteed
    'agentd.mountSignInGate is not a function' on load — a dead window, every launch.

    ONE rule for every component now, driven by each component's declared `requires`. Adding a
    second component needs no second rule, which is why the catalogue is injected rather than
    copied into this module.
    """
    found = sign_in("await agentd.mountSignInGate()", vendored="function fromPage(){}")
    assert found["UI_SDK_PREDATES_COMPONENT"].level == "error"
    assert "sign-in" in found["UI_SDK_PREDATES_COMPONENT"].message
    assert "mountSignInGate" in found["UI_SDK_PREDATES_COMPONENT"].message
    assert "re-vendor" in found["UI_SDK_PREDATES_COMPONENT"].fix


def test_no_vendored_sdk_present_is_not_an_error():
    """An agent may load the SDK from somewhere else. Absence is unknown, not wrong."""
    found = sign_in("await agentd.mountSignInGate()", vendored=None)
    assert not found


def test_an_agent_with_no_app_window_is_never_asked_to_sign_in():
    """No [app] table means no page exists to put a gate on."""
    sources = {"ui/app.js": "const client = agentd.fromPage()"}
    assert not RULES.check(None, {}, list(sources), sources)


def test_an_agent_with_no_ui_code_is_not_flagged():
    """Not scaffolded yet. Warning here would fire on every agent before its UI exists."""
    assert not RULES.check(None, APP, [], {})


def test_a_commented_out_gate_does_not_count():
    """The cry-wolf inverse: prose ABOUT the gate must not satisfy the rule, or a file that only
    mentions it in a comment would pass while shipping no login at all."""
    js = """
    // await agentd.mountSignInGate()   <- add this
    const client = agentd.fromPage()
    """
    assert "UI_NO_SIGN_IN" in sign_in(js)


# ── one sign-in implementation, and no agent may grow a second ──────────────
def test_an_app_that_talks_to_the_accounts_service_itself_is_refused():
    """THE REASON THIS RULE EXISTS. There were three copies of sign-in in this codebase and they
    drifted: one would not renew a token that had already expired, one had no single-flight guard
    (so two windows waking together got the whole refresh family revoked), and they posted to
    different endpoints. Users were signed out ten minutes in, and signing back in did not help.
    An agent that reaches past the shared implementation re-creates all of that for its own users
    only — the hardest kind to find."""
    found = sign_in(
        "await agentd.mountSignInGate();\n"
        "const r = await fetch(accounts + '/auth/refresh', {method:'POST'})"
    )
    assert "UI_OWN_LOGIN" in found
    assert found["UI_OWN_LOGIN"].level == "error"
    assert "/auth/refresh" in found["UI_OWN_LOGIN"].message


def test_the_fix_names_the_two_calls_that_replace_it():
    found = sign_in("fetch('/auth/login', {method:'POST'})")
    assert "mountSignInGate" in found["UI_OWN_LOGIN"].fix
    assert "accessToken" in found["UI_OWN_LOGIN"].fix


def test_keeping_a_refresh_token_is_refused_however_it_was_obtained():
    """The 30-day credential for the whole account. An agent holding one is the thing the desktop
    app goes out of its way never to hand down."""
    assert "UI_OWN_LOGIN" in sign_in(
        "await agentd.mountSignInGate();\n"
        "localStorage.setItem('mine', JSON.stringify({refresh_token: t}))"
    )


def test_writing_the_shared_storage_key_is_refused():
    """Two writers over one slot. The manager keeps the session there and renews it; an agent
    editing the same key hands the socket a credential nothing is looking after."""
    assert "UI_OWN_LOGIN" in sign_in(
        "await agentd.mountSignInGate(); localStorage.removeItem('agentd.session.mine')"
    )


def test_an_app_that_merely_HAS_a_login_page_is_left_alone():
    """Matched on the endpoint and the storage key, never on the word. An agent is welcome to a
    route, a button and a heading called login — a false alarm on working code is how a report
    teaches the model to ignore it."""
    found = sign_in(
        "await agentd.mountSignInGate();\n"
        "function showLogin(){ route('/login-help'); }  // login screen copy"
    )
    assert "UI_OWN_LOGIN" not in found


def test_an_ordinary_agent_using_the_sdk_is_left_alone():
    assert "UI_OWN_LOGIN" not in sign_in("await agentd.mountSignInGate()")

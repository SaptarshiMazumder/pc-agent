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

from agent_authoring.domain.ui_component import UiComponent, UiComponents
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


# EVERY FIXTURE IS A REACT APP, because that is the only kind there is. `app_sources` reads
# `app/src/**` and nothing else — the `ui/*.js` fallback went with the vanilla templates.
def check(js: str, path: str = "app/src/App.tsx"):
    return RULES.check(None, {}, ["app/package.json", path], {path: js})


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
    assert not RULES.check(None, {}, [], {"app/vendor/agentd-client.js": js})


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
SDK = "app/vendor/agentd-client.js"


# Every REQUIRED component, satisfied. A test about one of them has to satisfy the others, or the
# missing-component check short-circuits and the test reads a finding it was not asking about.
OTHER_REQUIRED = "\n".join(
    [
        "import Credits from './common/credits/Credits'",
        "import { Settings } from './common/settings/Settings'",
        "import OrgView from './common/orgs/OrgView'",
    ]
)


# NO REAL COMPONENT DECLARES A `requires` ANY MORE. All four reach the daemon through calls
# every SDK build has ever had — `authLogin`, `BillingClient`, `client.request` — so the vendored
# bundle's contents cannot make any of them fail, and these fixtures need nothing in particular
# inside it. The `requires` MECHANISM is still tested, with a component made up for the purpose;
# see `test_a_component_the_vendored_sdk_cannot_run_is_an_error`.
FULL_SDK = "function fromPage(){}"

# What a wired-up sign-in looks like: the shared module, rendered. The same shape as credits and
# settings, because it is now the same kind of thing — a copied React module, not an SDK call.
GATE = "import Gate from './common/auth/Gate'"


def sign_in(js: str, vendored: str | None = FULL_SDK):
    sources = {"app/package.json": "{}", "app/src/App.tsx": js + "\n" + OTHER_REQUIRED}
    if vendored is not None:
        sources[SDK] = vendored
    return {f.code: f for f in RULES.check(None, APP, list(sources), sources)}


def only(js: str, vendored: str | None = FULL_SDK):
    """The raw form: exactly the code given, nothing added. For the missing-component tests."""
    sources = {"app/package.json": "{}", "app/src/App.tsx": js}
    if vendored is not None:
        sources[SDK] = vendored
    return {f.code: f for f in RULES.check(None, APP, list(sources), sources)}


def test_an_app_agent_with_no_sign_in_is_refused():
    """MANDATORY, not advisory. It was a warning for exactly as long as it took to publish past
    one — an agent with a window has to know who is using it, and on a hosted install every model
    call fails without it, with nothing on screen to explain why. The rulebook also closes PACK
    and PUBLISH on this code, so an agent that skips it cannot ship at all."""
    found = only("const client = agentd.fromPage()")
    assert "UI_NO_SIGN_IN" in found
    assert found["UI_NO_SIGN_IN"].level == "error"
    # The fix is the INSTRUCTION, not a tool name. It used to name `add_ui_component`, which does
    # its work through <script> tags, style.css appends and app.js splicing — vanilla-era
    # mechanisms that no longer exist now every agent UI is React. Pointing at a tool that cannot
    # finish the job sends the model down a path where half the steps silently do nothing.
    assert "common/auth" in found["UI_NO_SIGN_IN"].fix
    assert "main.tsx" in found["UI_NO_SIGN_IN"].fix
    assert "add_ui_component" not in found["UI_NO_SIGN_IN"].fix


def test_rendering_the_gate_is_clean():
    assert "UI_NO_SIGN_IN" not in sign_in(GATE)


def test_the_shared_module_is_the_only_way_to_satisfy_sign_in():
    """ONE DOOR NOW, where there were two. `signInFirst` was the common module's wrapper and
    `mountSignInGate` the SDK call underneath it — a vanilla-DOM gate that painted itself over the
    page, written for the vanilla templates and never used by the assistant, which has always had
    the React card now copied into `_common/auth/`. Both are deleted, so an agent still calling
    either is an agent built before the port and does not have a login screen this platform knows.

    The detector once accepted `resolveAuth` and a bare `signIn` too, which blessed an agent
    driving its own sign-in surface. That is now exactly what `UI_OWN_LOGIN` refuses: one
    implementation of credentials on this platform, or every agent gets its own set of renewal
    bugs."""
    assert "UI_NO_SIGN_IN" in only("await signInFirst('My Agent')")
    assert "UI_NO_SIGN_IN" in only("await mountSignInGate({ client })")
    # And the card itself counts, for an app that lays out its own gating.
    assert "UI_NO_SIGN_IN" not in sign_in("import SignIn from './common/auth/SignIn'")


# --- the vendored SDK predating the catalogue -------------------------------------------
# A MADE-UP COMPONENT, because no real one declares a `requires` any more: sign-in, credits and
# settings all reach the daemon through calls every SDK build has ever had. That is a fact about
# today's three components, not about the mechanism — agents vendor a SNAPSHOT of the SDK, so the
# day a fourth component needs a NEW symbol, this is the only thing standing between it and a
# window that dies on load. Testing it through a fixture keeps the check honest instead of
# deleting it and finding out later.
LATER = UiComponent(
    id="later",
    title="A component added after some agent vendored its SDK",
    summary="stands in for the real one that will need a new SDK symbol",
    detect=(r"\bmountSomethingNew\s*\(",),
    requires=("mountSomethingNew",),
)
DRIFTED = UiRules(
    events=APP_FACING_EVENTS,
    kinds=MESSAGE_UPDATE_KINDS,
    methods=frozenset(APP_SCOPED_METHODS),
    sdk_methods=frozenset(),
    components=UiComponents().all() + (LATER,),
)


def drifted(js: str, vendored: str | None):
    """Every REQUIRED component satisfied, plus the made-up one — so the only thing the rule can
    have to say is about the vendored bundle."""
    sources = {"app/package.json": "{}", "app/src/App.tsx": js + "\n" + GATE + "\n" + OTHER_REQUIRED}
    if vendored is not None:
        sources[SDK] = vendored
    return {f.code: f for f in DRIFTED.check(None, APP, list(sources), sources)}


def test_a_component_whose_sdk_symbol_is_missing_is_an_error():
    """The drift case: the app was updated, ui/vendor/agentd-client.js was not. Guaranteed
    'agentd.mountSomethingNew is not a function' on load — a dead window, every launch.

    ONE rule for every component, driven by each component's declared `requires`. Adding a
    component needs no second rule, which is why the catalogue is injected rather than copied
    into this module — and why this test can introduce one the catalogue has never heard of.
    """
    found = drifted("await agentd.mountSomethingNew()", vendored="function fromPage(){}")
    assert found["UI_SDK_PREDATES_COMPONENT"].level == "error"
    assert "later" in found["UI_SDK_PREDATES_COMPONENT"].message
    assert "mountSomethingNew" in found["UI_SDK_PREDATES_COMPONENT"].message
    assert "re-vendor" in found["UI_SDK_PREDATES_COMPONENT"].fix


def test_the_symbol_being_present_is_quiet():
    """The other half: the rule reads the bundle, it does not merely notice that one exists."""
    assert not drifted("await agentd.mountSomethingNew()", "function mountSomethingNew(){}")


def test_no_vendored_sdk_present_is_not_an_error():
    """An agent may load the SDK from somewhere else. Absence is unknown, not wrong."""
    assert not drifted("await agentd.mountSomethingNew()", vendored=None)


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


def test_the_fix_names_what_replaces_it():
    found = sign_in("fetch('/auth/login', {method:'POST'})")
    assert "common/auth" in found["UI_OWN_LOGIN"].fix
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


def test_an_app_agent_with_no_credits_panel_is_refused():
    """MANDATORY for the same reason sign-in is, one step later in the same story.

    Running out of credits is the ONE failure a user can fix themselves. Without this panel the
    agent stops working and says nothing about why or where to go — the user has to already know
    that a separate app exists, find it, and top up there. Every agent that can spend credits can
    sell them.
    """
    found = only("await agentd.mountSignInGate()")
    assert "UI_NO_CREDITS" in found
    assert found["UI_NO_CREDITS"].level == "error"
    # It must say the thing that is actually missing. `Credits.tsx` ships with every React
    # scaffold, so "add the component" is advice the author has already followed; what they have
    # not done is RENDER it, and the fix has to say so in those words.
    assert "SHIPPING IT IS NOT ENOUGH" in found["UI_NO_CREDITS"].fix
    assert "<Credits />" in found["UI_NO_CREDITS"].fix
    assert "add_ui_component" not in found["UI_NO_CREDITS"].fix


def test_every_missing_component_is_reported_in_one_pass():
    """A queue of round trips is not a report. An author fixing three one-line omissions should
    see all of them the first time, not find the next after fixing the last.

    The set is asserted EXACTLY, so adding another required component fails here — which is the
    reminder to check that its message is written for somebody who has never seen it. It has
    already earned that once: `orgs` was added and this test is where it was noticed."""
    found = only("const client = agentd.fromPage()")
    assert set(found) == {"UI_NO_SIGN_IN", "UI_NO_CREDITS", "UI_NO_SETTINGS", "UI_NO_ORGS"}


def test_an_app_that_has_both_is_quiet():
    """The whole point: the templates ship both, so a freshly scaffolded agent passes untouched."""
    found = sign_in(GATE)
    assert not found


def test_the_shared_module_is_the_only_way_to_satisfy_credits():
    """`mountCreditsPanel` was the SDK's vanilla panel and is deleted — agentd never used it, and
    the React page it always had is what `_common/credits/` now carries. An agent still calling it
    is an agent built before the port, and it no longer counts as having a credits page."""
    assert "UI_NO_CREDITS" in only("await agentd.mountCreditsPanel({ mount: el })")


def test_none_of_the_required_components_can_outrun_a_vendored_sdk():
    """THE REAL CATALOGUE, against a bundle with nothing in it. All three required components
    reach the daemon through calls that have existed in every SDK build there has ever been —
    `authLogin`, `BillingClient`, `client.request` — so none of them declares a `requires`, and a
    stale bundle cannot break them. Naming a symbol for any of them would be a check that can
    never fire, which reads like coverage and is worse than none.

    The mechanism that WOULD catch a component with a new symbol is tested above, on one made up
    for the purpose."""
    assert not sign_in(GATE, vendored="function nothingUseful(){}")


# --- shipped is not the same as wired ---------------------------------------
# The React starter DELIVERS `Credits.tsx`, whose whole body is a call to `mountCreditsPanel`.
# A scan of every source file found that call inside the definition, so an agent that never
# rendered `<Credits />` passed the check that existed to prove it had: a credits page shipped,
# validated, and invisible. These pin the distinction from both sides.

# What the scaffolded entry point looks like: the gate wrapping the app.
SIGN_IN_MAIN = "import Gate from './common/auth/Gate'"


STARTER_CREDITS = (
    "import { mountCreditsPanel } from '@agentd/client'\n"
    "export default function Credits() { void mountCreditsPanel({}) ; return <div/> }"
)


def react(app_tsx: str) -> dict:
    """A React agent: source in app/src, the starter files, and whatever App.tsx says.

    Every REQUIRED component except the one under test is satisfied here. A fixture that left one
    missing would make each test read a finding it was not asking about — and the tests below are
    about which spellings of "used" count, not about how many components exist.
    """
    sources = {
        "app/package.json": "{}",
        "app/src/main.tsx": SIGN_IN_MAIN,
        "app/src/Credits.tsx": STARTER_CREDITS,
        "app/src/Config.tsx": "import { Settings } from './common/settings/Settings'",
        "app/src/Team.tsx": "import OrgView from './common/orgs/OrgView'",
        "app/src/App.tsx": app_tsx,
    }
    return {f.code: f for f in RULES.check(None, APP, list(sources), sources)}


def test_a_shipped_credits_file_nobody_renders_is_still_missing():
    """THE HOLE. `Credits.tsx` arrives with every React scaffold, so its presence proves only that
    the scaffolder ran. An agent whose window never renders it has no credits page, and the user
    who runs out of credits inside it has nowhere to go."""
    found = react("export default function App() { return <div>hi</div> }")
    assert "UI_NO_CREDITS" in found
    assert found["UI_NO_CREDITS"].level == "error"


def test_importing_the_shared_module_counts():
    assert not react("import Credits from './common/credits/Credits'")


def test_a_page_of_its_own_called_credits_does_not_count():
    """THE POINT IS THAT IT IS THE SAME SHOP. This used to accept `<Credits />`, an import of any
    file named Credits, or a direct `mountCreditsPanel` call — so an agent satisfied the rule with
    a hand-written billing page of its own, which is the outcome the rule exists to refuse.
    Somebody who tops up in the assistant and then inside an agent must not meet two different
    stores with two sets of prices."""
    assert "UI_NO_CREDITS" in react("import Credits from './components/Credits'")


def test_sign_in_is_unaffected_by_the_provides_rule():
    """sign-in ships no file of its own, so there is nowhere for its call to hide. Its check must
    behave exactly as before — a component with `provides=()` is not quietly re-scoped."""
    assert "UI_NO_SIGN_IN" not in react("export default function App() { return <Credits /> }")
    bare = {
        "app/package.json": "{}",
        "app/src/App.tsx": "export default function App() { return <Credits /> }",
        "app/src/Credits.tsx": STARTER_CREDITS,
    }
    found = {f.code: f for f in RULES.check(None, APP, list(bare), bare)}
    assert "UI_NO_SIGN_IN" in found

"""The UI COMPONENT tier — reusable pieces addable to an app that already exists.

The gap it fills: this bundle had one kind of reuse, whole-app templates, and `scaffold_ui` refuses
over an existing `ui/`. So "add sign-in to an agent someone already built" had no route — only
re-scaffold (destroying their work) or hand-edit. Sign-in is simply the first thing that wanted a
smaller unit.

What these tests pin, in order of how much it matters:
  1. IDEMPOTENCE. Re-applying changes nothing. Without this the tool cannot be trusted by a model.
  2. NO GUESSING. No anchor -> the code is handed back, not regexed into unfamiliar code.
  3. ONE OWNER for the snippet — the descriptor, checked against the template and the validator.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_authoring.application.add_component_service import (
    BLOCKED,
    DONE,
    MANUAL,
    PRESENT,
    AddComponentService,
    ComponentError,
)
from agent_authoring.domain.ui_component import (
    COMPONENTS_ANCHOR,
    SIGN_IN,
    SIGNIN_ANCHOR,
    UiComponents,
)

SDK_WITH_GATE = "function mountSignInGate(){}; function fromPage(){}"

# Two anchors, because sign-in is placed differently from everything else: it must run BEFORE the
# connection (a hosted socket needs the session the gate mints), while every other component wants
# the socket already open. See SIGNIN_ANCHOR's comment.
APP_JS = f"""const client = agentd.fromPage()
// {SIGNIN_ANCHOR} — the gate goes here, above the connection wiring.
client.onState((s) => {{
  if (s === 'open') {{
    void (async () => {{
      // {COMPONENTS_ANCHOR} — add_ui_component inserts after this line.
      const hello = await client.hello()
    }})()
  }}
}})
"""

HAND_WRITTEN_APP_JS = """const client = agentd.fromPage()
async function boot() {
  const hello = await client.hello()
}
boot()
"""

INDEX_HTML = """<!doctype html>
<html><body>
  <div id="app"></div>
  <script src="app.js"></script>
</body></html>
"""


class FakeReader:
    def __init__(self, agents: dict):
        self._agents = agents

    def agent_dir(self, agent_id):
        return self._agents.get(agent_id)

    def known_ids(self):
        return sorted(self._agents)


@pytest.fixture
def workspace(tmp_path):
    """An agent with a scaffolded app, plus the roots the service copies from."""
    agent = tmp_path / "agents" / "weather"
    ui = agent / "ui"
    (ui / "vendor").mkdir(parents=True)
    (ui / "app.js").write_text(APP_JS, encoding="utf-8")
    (ui / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (ui / "style.css").write_text(":root { --accent: #8ab4f8; }\n", encoding="utf-8")
    (ui / "vendor" / "agentd-client.js").write_text("function fromPage(){}", encoding="utf-8")

    borrow = tmp_path / "builder-ui"
    (borrow / "vendor").mkdir(parents=True)
    (borrow / "vendor" / "agentd-client.js").write_text(SDK_WITH_GATE, encoding="utf-8")

    components = UiComponents()
    service = AddComponentService(
        FakeReader({"weather": agent}), components, tmp_path / "components", borrow
    )
    return service, ui


def states(plan) -> dict:
    """Keyed by (kind, target), NOT by target alone.

    One path legitimately appears twice — `vendor/agentd-client.js` is both a FILE to copy and a
    <script> tag to ensure — and a target-keyed dict silently dropped whichever came first, which
    hid a step from these assertions entirely.
    """
    return {(s.kind, s.target): s.state for s in plan.steps}


# ── the happy path ───────────────────────────────────────────────────────────────────────


def test_adding_sign_in_does_every_step(workspace):
    service, ui = workspace
    plan = service.apply(service.plan("weather", "sign-in"))

    assert states(plan) == {
        ("file", "vendor/agentd-client.js"): DONE,  # refreshed — a stale SDK IS the "not a function" bug
        ("script", "vendor/agentd-client.js"): DONE,  # the <script> tag index.html was missing
        ("style", "style.css"): DONE,  # theme tokens, so it does not look bolted on
        ("insert", "app.js"): DONE,  # the call itself
    }
    app = (ui / "app.js").read_text(encoding="utf-8")
    assert "await agentd.mountSignInGate({ client })" in app
    assert (ui / "vendor" / "agentd-client.js").read_text() == SDK_WITH_GATE
    assert "--gate-bg" in (ui / "style.css").read_text(encoding="utf-8")
    # The SDK was refreshed by this very call, so nothing should be reported as missing.
    assert service.missing_sdk_symbols(plan) == []


def test_the_snippet_is_indented_to_match_the_anchor(workspace):
    """A patch that lands at the wrong indentation reads as machine-mangled code and gets reverted.

    Derived from the descriptor rather than hardcoded: the two are the same fact, and a literal
    here would silently disagree the next time the placement moves."""
    service, ui = workspace
    service.apply(service.plan("weather", "sign-in"))
    lines = (ui / "app.js").read_text(encoding="utf-8").splitlines()
    indent = SIGN_IN.insert[0].indent

    opener = next(l for l in lines if l.strip().startswith("void (async"))
    assert opener.startswith(indent) and not opener.startswith(indent + " ")

    # The call sits two nestings deeper than the block that carries it — its own async wrapper,
    # then the try{}. The wrapper is what lets the boot continue without awaiting sign-in.
    call = next(l for l in lines if "mountSignInGate" in l)
    assert call.startswith(indent + "    ")


def test_the_script_tag_is_added_before_app_js(tmp_path, workspace):
    """Load ORDER is the bug this prevents: the SDK must be defined before app.js runs."""
    service, ui = workspace
    (ui / "index.html").write_text(
        "<!doctype html>\n<html><body>\n  <script src=\"app.js\"></script>\n</body></html>\n",
        encoding="utf-8",
    )
    service.apply(service.plan("weather", "sign-in"))
    html = (ui / "index.html").read_text(encoding="utf-8")
    assert html.index("vendor/agentd-client.js") < html.index('src="app.js"')


def test_a_cache_busted_script_src_counts_as_present(workspace):
    """figure-creator loads the SDK as `vendor/agentd-client.js?v=4`. A literal string compare said
    "not present" and added a SECOND <script> for the same file — the SDK loaded twice. The query
    string is not part of which file is loaded."""
    service, ui = workspace
    (ui / "index.html").write_text(
        INDEX_HTML.replace(
            '<script src="app.js">',
            '<script src="vendor/agentd-client.js?v=4"></script>\n  <script src="app.js">',
        ),
        encoding="utf-8",
    )
    plan = service.apply(service.plan("weather", "sign-in"))
    assert states(plan)[("script", "vendor/agentd-client.js")] == PRESENT
    assert (ui / "index.html").read_text(encoding="utf-8").count("agentd-client.js") == 1


def test_an_existing_script_tag_is_not_duplicated(workspace):
    service, ui = workspace
    (ui / "index.html").write_text(
        INDEX_HTML.replace(
            '<script src="app.js">', '<script src="vendor/agentd-client.js"></script>\n  <script src="app.js">'
        ),
        encoding="utf-8",
    )
    plan = service.apply(service.plan("weather", "sign-in"))
    assert states(plan)[("script", "vendor/agentd-client.js")] == PRESENT
    html = (ui / "index.html").read_text(encoding="utf-8")
    assert html.count("vendor/agentd-client.js") == 1


# ── IDEMPOTENCE: the property that makes this usable by a model ───────────────────────────


def test_applying_twice_changes_nothing(workspace):
    service, ui = workspace
    service.apply(service.plan("weather", "sign-in"))
    after_first = (ui / "app.js").read_text(encoding="utf-8")
    css_after_first = (ui / "style.css").read_text(encoding="utf-8")

    second = service.plan("weather", "sign-in")

    assert second.nothing_to_do
    assert states(second)[("insert", "app.js")] == PRESENT
    assert states(second)[("style", "style.css")] == PRESENT
    service.apply(second)
    assert (ui / "app.js").read_text(encoding="utf-8") == after_first
    assert (ui / "style.css").read_text(encoding="utf-8") == css_after_first
    assert after_first.count("mountSignInGate") == 1


def test_an_app_that_signs_in_its_own_way_is_left_alone(workspace):
    """figure-creator drives sign-in with resolveAuth/signIn instead of the modal. Forcing the
    drop-in gate on it would be the tool dictating design rather than filling a gap."""
    service, ui = workspace
    (ui / "app.js").write_text(
        APP_JS.replace("const hello", "const auth = await agentd.resolveAuth()\n      const hello"),
        encoding="utf-8",
    )
    plan = service.plan("weather", "sign-in")
    assert states(plan)[("insert", "app.js")] == PRESENT
    assert "already signs the user in" in next(
        s.detail for s in plan.steps if s.target == "app.js"
    )


# ── NO GUESSING at code it does not recognise ────────────────────────────────────────────


def test_a_hand_written_app_gets_the_snippet_handed_back(workspace):
    """No anchor. Every file-level step still runs; the CODE is stated, not inserted. A regex that
    guessed could land inside a string, a comment, or the wrong function — a file that looks
    patched and does not run."""
    service, ui = workspace
    (ui / "app.js").write_text(HAND_WRITTEN_APP_JS, encoding="utf-8")

    plan = service.apply(service.plan("weather", "sign-in"))

    assert states(plan)[("insert", "app.js")] == MANUAL
    assert states(plan)[("file", "vendor/agentd-client.js")] == DONE  # deterministic steps ran
    assert states(plan)[("style", "style.css")] == DONE
    assert (ui / "app.js").read_text(encoding="utf-8") == HAND_WRITTEN_APP_JS  # untouched
    step = plan.manual[0]
    assert "mountSignInGate" in step.payload
    assert SIGNIN_ANCHOR in step.detail
    # The note has to say WHERE, and the placement that matters is "before the connection" — a
    # gate placed after it cannot run on a hosted daemon at all.
    assert "BEFORE the connection" in step.detail


# ── refusals ─────────────────────────────────────────────────────────────────────────────


def test_an_unknown_component_lists_what_there_is(workspace):
    service, _ = workspace
    with pytest.raises(ComponentError, match="no ui component 'teleport'"):
        service.plan("weather", "teleport")


def test_an_unknown_agent_names_the_known_ones(workspace):
    service, _ = workspace
    with pytest.raises(ComponentError, match="Known agents: weather"):
        service.plan("nope", "sign-in")


def test_an_agent_with_no_ui_is_sent_to_scaffold_first(tmp_path):
    agent = tmp_path / "agents" / "bare"
    agent.mkdir(parents=True)
    service = AddComponentService(
        FakeReader({"bare": agent}), UiComponents(), tmp_path / "c", tmp_path / "b"
    )
    with pytest.raises(ComponentError, match="scaffold_ui first"):
        service.plan("bare", "sign-in")


def test_a_missing_borrow_source_blocks_and_writes_nothing(tmp_path, workspace):
    """The SDK is copied from the LIVE ui/ so there is only ever one copy in a product. If that is
    gone, this install is broken — a soft skip would ship an app whose SDK cannot sign in."""
    service, ui = workspace
    before = (ui / "app.js").read_text(encoding="utf-8")
    # remove the borrow source
    borrowed = next(
        p for p in tmp_path.rglob("builder-ui/vendor/agentd-client.js")
    )
    borrowed.unlink()

    plan = service.plan("weather", "sign-in")
    assert states(plan)[("file", "vendor/agentd-client.js")] == BLOCKED
    with pytest.raises(ComponentError):
        service.apply(plan)
    assert (ui / "app.js").read_text(encoding="utf-8") == before


def test_a_stale_live_sdk_is_reported_after_applying(tmp_path, workspace):
    """Reported AFTER applying on purpose: applying refreshes the SDK, so flagging a staleness the
    same call just fixed is the false alarm that gets a check ignored. If it still fires, the LIVE
    SDK genuinely lacks the symbol and rebuilding it is the fix."""
    service, ui = workspace
    borrowed = next(p for p in tmp_path.rglob("builder-ui/vendor/agentd-client.js"))
    borrowed.write_text("function fromPage(){}", encoding="utf-8")  # no gate

    plan = service.apply(service.plan("weather", "sign-in"))
    assert service.missing_sdk_symbols(plan) == ["mountSignInGate"]


# ── ONE owner for the snippet ────────────────────────────────────────────────────────────


def test_the_template_carries_the_anchor_and_the_descriptor_snippet():
    """The drift this closes: the sign-in call lived in the chat-app template, in the validator's
    regex, and (now) in the component — three unshared copies of one fact. The descriptor owns it;
    this asserts the template agrees, so a change to one is caught rather than discovered."""
    template = (
        Path(__file__).resolve().parents[2]
        / "agents/agent-builder/skills/build-agent/templates/chat-app/app.js"
    )
    text = template.read_text(encoding="utf-8")
    assert COMPONENTS_ANCHOR in text, "components can only be placed deterministically via the anchor"
    for line in SIGN_IN.insert[0].snippet.splitlines():
        assert line.strip() in text, f"template drifted from the descriptor: {line.strip()!r}"


def test_the_validator_and_the_tool_share_one_definition_of_present():
    """UiRules is GIVEN this catalogue, so 'is sign-in present?' and 'what does adding it write?'
    cannot disagree. This asserts the descriptor's own snippet satisfies its own detector — the
    minimum for those two answers to be consistent."""
    insertion = SIGN_IN.insert[0]
    assert insertion.present_in(insertion.snippet)


def test_the_catalogue_describes_itself_for_the_tool():
    components = UiComponents()
    assert components.ids() == ("sign-in",)
    assert "sign-in —" in components.describe()
    assert components.get("nope") is None
    # Every component must declare how to detect it, or the tier stops being idempotent.
    for component in components.all():
        assert component.insert, f"{component.id} declares no insertion"
        for insertion in component.insert:
            assert insertion.detect and insertion.snippet

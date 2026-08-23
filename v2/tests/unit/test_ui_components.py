"""The UI component CATALOGUE — what a mandatory piece of an agent's window is, and how to
recognise one that is already there.

WHAT THIS FILE USED TO BE. Most of it tested `AddComponentService`, the patcher behind the
`add_ui_component` tool: it added a `<script src>` tag to index.html, appended theme tokens to
style.css and spliced a snippet into app.js. All three are vanilla-era mechanisms, and the vanilla
templates are gone — every agent window is a React project now, compiled from `app/src`. A patcher
that could do half its steps and report success is worse than no patcher, so the tool and the
service were deleted and those tests with them.

THE CATALOGUE IS NOT THE PATCHER, and only the patcher went. `UiComponents` is what tells
`validate_agent` which pieces are mandatory (`UI_NO_SIGN_IN`, `UI_NO_CREDITS`) and how to spot one
in source, so it is load-bearing for every agent that ships. That is what remains under test here;
the rules that consume it are covered in test_ui_rules.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_authoring.domain.ui_component import CREDITS, SETTINGS, SIGN_IN, UiComponents


def test_each_detector_matches_the_code_it_describes():
    """UiRules is GIVEN this catalogue, so "is sign-in present?" and "what does wiring it look
    like?" cannot disagree. These are the exact shapes the common modules and the skill tell an
    author to write; a detector that missed one would make the validator demand a thing and then
    fail to see it once written."""
    assert SIGN_IN.present_in("import Gate from './common/auth/Gate'")
    assert SIGN_IN.present_in("import SignIn from './common/auth/SignIn'")
    assert CREDITS.present_in("import Credits from './common/credits/Credits'")
    assert SETTINGS.present_in("import { Settings } from './common/settings/Settings'")


def test_a_detector_does_not_fire_on_unrelated_code():
    """Conservative by design: a false alarm on working code teaches the model to ignore the
    report, which costs more than a missed defect."""
    assert not SIGN_IN.present_in("function signInLater() {}")
    # The deleted vanilla gate. An agent still calling it is one built before the port, and it
    # does not have the login screen this platform knows.
    assert not SIGN_IN.present_in("await mountSignInGate({ client })")
    assert not SIGN_IN.present_in("await signInFirst('My Agent')")
    # A login card of the agent's own — the same refusal credits gets, for the same reason.
    assert not SIGN_IN.present_in("import SignIn from './components/SignIn'")
    assert not CREDITS.present_in("const creditsRemaining = 4")
    # A billing page of the agent's own is the case this rule refuses: the shop has to be THE
    # shop, or somebody meets two stores with two sets of prices.
    assert not CREDITS.present_in("import Credits from './components/Credits'")
    assert not CREDITS.present_in("await mountCreditsPanel({ mount })")
    # Using the HOOK is not showing the PAGE. An agent may read a setting for its own purposes
    # without ever giving the user somewhere to change one, and that is the case this rule exists
    # to catch — so the hook must not satisfy it.
    assert not SETTINGS.present_in("const s = useSettings(client, agentId)")
    # A hand-written page that happens to be CALLED Settings is the case this rule exists to
    # refuse — the whole point is that the page is the same one everywhere, not that a component
    # somewhere shares its name.
    assert not SETTINGS.present_in("import { Settings } from './components/Settings'")


def test_the_catalogue_describes_itself():
    components = UiComponents()
    assert components.ids() == ("sign-in", "credits", "settings")
    assert "sign-in —" in components.describe()
    assert "credits —" in components.describe()
    assert "settings —" in components.describe()
    assert components.get("nope") is None


def test_every_component_declares_how_to_detect_it():
    """Without a detector the validator cannot tell a wired component from a missing one, and the
    finding it drives becomes either always-on or always-off."""
    for component in UiComponents().all():
        assert component.detect, f"{component.id} declares no detector"


def test_no_required_component_names_an_sdk_symbol():
    """`requires` is what catches an agent whose vendored SDK predates a component's SDK call —
    the 'not a function' dead window. NONE OF THE THREE NEEDS IT.

    Each reaches the daemon through a call that has existed in every SDK build there has ever
    been: `authLogin` for sign-in, BillingClient for credits, `client.request(...)` for settings.
    Naming a symbol for any of them would be a check that can never fire, and a check that always
    passes is worse than no check because it reads like coverage.

    This is pinned rather than left unsaid because it changed: sign-in declared `mountSignInGate`
    until that vanilla gate was deleted in favour of the assistant's React card. The mechanism is
    still live and still tested — see `test_a_component_whose_sdk_symbol_is_missing_is_an_error`
    in test_ui_rules.py, which introduces a component the catalogue has never heard of."""
    for component in UiComponents().all():
        assert component.requires == (), (
            f"{component.id} names an SDK symbol. If that is deliberate, this test is the thing "
            f"to change — but check first that the symbol is genuinely newer than some agent's "
            f"vendored bundle, because otherwise it is a check that can never fire."
        )


def test_a_component_that_ships_a_file_marks_it_as_its_own():
    """`provides` marks files that merely DEFINE a component, so they are not counted as evidence
    that anything uses it. All three arrive with every React scaffold and would otherwise satisfy
    their own checks by existing.

    Sign-in declared nothing until recently, when it lived entirely in the SDK and had no file of
    its own for a call to hide in. It is a copied module like the other two now."""
    assert CREDITS.provides == ("Credits.tsx",)
    assert SETTINGS.provides == ("Settings.tsx",)
    assert SIGN_IN.provides == ("SignIn.tsx", "Gate.tsx")

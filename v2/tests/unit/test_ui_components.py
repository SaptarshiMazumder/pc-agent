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

from agent_authoring.domain.ui_component import CREDITS, SIGN_IN, UiComponents


def test_a_descriptors_own_snippet_satisfies_its_own_detector():
    """UiRules is GIVEN this catalogue, so "is sign-in present?" and "what does wiring it look
    like?" cannot disagree. A descriptor whose snippet does not match its own detector would make
    the validator demand a thing and then fail to see it once written."""
    for component in (SIGN_IN, CREDITS):
        for insertion in component.insert:
            assert insertion.present_in(insertion.snippet), component.id


def test_the_catalogue_describes_itself():
    components = UiComponents()
    assert components.ids() == ("sign-in", "credits")
    assert "sign-in —" in components.describe()
    assert "credits —" in components.describe()
    assert components.get("nope") is None


def test_every_component_declares_how_to_detect_it():
    """Without a detector the validator cannot tell a wired component from a missing one, and the
    finding it drives becomes either always-on or always-off."""
    for component in UiComponents().all():
        assert component.insert, f"{component.id} declares no insertion"
        for insertion in component.insert:
            assert insertion.detect, f"{component.id} has an insertion with no detector"
            assert insertion.snippet, f"{component.id} has an insertion with no snippet"


def test_every_component_names_the_sdk_symbols_it_needs():
    """`requires` is what catches an agent calling a panel its vendored SDK predates — the
    'not a function' dead window. A component that declares none cannot be checked for it."""
    for component in UiComponents().all():
        assert component.requires, f"{component.id} declares no required SDK symbols"


def test_only_credits_ships_a_file_of_its_own():
    """`provides` marks files that merely DEFINE a component, so they are not counted as evidence
    that anything uses it. Credits ships `Credits.tsx` with every React scaffold and therefore
    needs it; sign-in lives entirely in the SDK, so there is nowhere for its call to hide and
    marking anything would only weaken the check."""
    assert CREDITS.provides == ("Credits.tsx",)
    assert SIGN_IN.provides == ()

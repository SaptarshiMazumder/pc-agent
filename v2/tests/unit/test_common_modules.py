"""The shared modules are copied into every agent — so something has to check they still match.

`app/src/common/` holds accounts and money. It is COPIED rather than imported, because an agent is
a shipped artifact and no workspace path survives being packaged, published and downloaded onto
somebody else's machine. Copying is how the code gets there; it is not what keeps it right.

A copy is editable, and the edits are always reasonable at the time — a colour, a label, a
"temporary" change to get past something. What ends up different is credential handling and payment
handling, in an artifact that is then published. And it still BUILDS, which is what makes it
invisible to every other check.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_authoring.domain.common_module_rules import COMMON_DIR, CommonModuleRules

CANON = {"auth/SignIn.tsx": "export const signIn = 1\n", "credits/Credits.tsx": "export const c = 2\n"}
APP = {"app": {"width": 900}}
FILES = ["agent.toml", "app/package.json", f"{COMMON_DIR}auth/SignIn.tsx"]


def check(sources: dict, files=None, raw=None, canon=None):
    rules = CommonModuleRules(CANON if canon is None else canon)
    return {f.code: f for f in rules.check(None, APP if raw is None else raw, files or FILES, sources)}


def _copied() -> dict:
    return {f"{COMMON_DIR}{rel}": text for rel, text in CANON.items()}


def test_a_verbatim_copy_is_silent():
    assert not check(_copied())


def test_line_endings_are_not_content():
    """Git checks these out CRLF on Windows and LF elsewhere. A byte comparison would fail on the
    PLATFORM rather than on the content — a rule nobody can satisfy is a rule everybody turns off."""
    crlf = {k: v.replace("\n", "\r\n") for k, v in _copied().items()}
    assert not check(crlf)


def test_an_edited_module_is_an_error():
    """THE DANGEROUS ONE. It still builds, so nothing else in the toolchain notices."""
    edited = _copied()
    edited[f"{COMMON_DIR}auth/SignIn.tsx"] = "export const signIn = 999\n"
    found = check(edited)
    assert found["UI_COMMON_MODIFIED"].level == "error"
    assert "auth/SignIn.tsx" in found["UI_COMMON_MODIFIED"].path
    # The fix has to say where the change belongs when it is genuinely wanted, or the next author
    # edits the copy again for the same good reason.
    assert "templates/_common" in found["UI_COMMON_MODIFIED"].fix


def test_a_missing_module_is_an_error():
    missing = _copied()
    del missing[f"{COMMON_DIR}credits/Credits.tsx"]
    found = check(missing)
    assert found["UI_COMMON_MISSING"].level == "error"
    assert "credits/Credits.tsx" in found["UI_COMMON_MISSING"].path


def test_every_drifted_module_is_reported_at_once():
    """A queue of round trips is not a report — an author should see all of it the first time."""
    broken = {f"{COMMON_DIR}auth/SignIn.tsx": "nope\n"}  # one edited, one absent
    found = check(broken)
    assert set(found) == {"UI_COMMON_MODIFIED", "UI_COMMON_MISSING"}


# --- when the rule must stay quiet -------------------------------------------


def test_an_agent_with_no_window_is_not_asked_for_them():
    """No `[app]`, no window, nothing to sign into. Warning here is how a check earns its way into
    being ignored."""
    assert not check({}, raw={})


def test_an_agent_that_has_not_been_scaffolded_is_not_asked_either():
    """`[app]` declared but no `app/` yet — mid-build, not broken."""
    assert not check({}, files=["agent.toml", "ui/index.html"])


def test_no_catalogue_means_no_opinion():
    """A build that shipped without the authoring templates cannot compare anything. Silence beats
    inventing a failure out of our own missing data — otherwise every agent on that build is
    reported as broken by a rule that simply cannot see."""
    assert not check(_copied(), canon={})


# --- the token contract -----------------------------------------------------
# The other half of the bargain. The shared modules carry NO colours and NO fonts — every visual
# property is a var() — so that each agent can look like itself. An agent that does not define the
# names ships pages that are structurally perfect and visually blank, and it BUILDS, which is what
# makes this the same class of failure as a silent edit.
#
# The bug that put this here: a sample with a dark shell and a white settings page in the middle
# of it. Its palette was under its own names (`--ink`, `--hair`); the modules were reading names
# nobody had defined; the credits balance rendered grey on white. Validation passed.

STYLED = {"auth/auth.css": ".card { color: var(--text); background: var(--bg2); }"}


def styled(agent_css: str | None, canon=None):
    """The modules copied verbatim, plus whatever stylesheet the agent has."""
    canon = STYLED if canon is None else canon
    sources = {f"{COMMON_DIR}{rel}": text for rel, text in canon.items()}
    if agent_css is not None:
        sources["app/src/styles.css"] = agent_css
    return check(sources, files=["agent.toml", "app/package.json"], canon=canon)


def test_an_app_that_defines_none_of_the_tokens_is_refused():
    found = styled(".shell { display: grid }")
    assert found["UI_TOKENS_MISSING"].level == "error"
    assert "--text" in found["UI_TOKENS_MISSING"].message
    assert "--bg2" in found["UI_TOKENS_MISSING"].message
    assert "tokens.css" in found["UI_TOKENS_MISSING"].fix


def test_defining_them_is_silent():
    assert not styled(":root { --text: #fff; --bg2: #222; }")


def test_an_alias_counts_as_a_definition():
    """THE RECOMMENDED FIX for an agent that already has a palette under its own names. Custom
    properties resolve where they are USED, so an alias carries the agent's theme — including a
    dark-mode block — into the shared pages. Restating the colours instead is what produces a
    palette that only knows about one of two themes."""
    assert not styled(":root { --text: var(--ink); --bg2: var(--raised); }")


def test_a_token_split_across_two_stylesheets_counts():
    """A token may live in `tokens.css` and another in `styles.css`. There is no ordering to
    respect — the check is only whether the name exists in the agent's own CSS at all."""
    sources = {f"{COMMON_DIR}{rel}": text for rel, text in STYLED.items()}
    sources["app/src/tokens.css"] = ":root { --text: #fff; }"
    sources["app/src/styles.css"] = ":root { --bg2: #222; }"
    assert not check(sources, files=["agent.toml", "app/package.json"], canon=STYLED)


def test_a_module_cannot_satisfy_its_own_contract():
    """The same hole `provides` closes for components. These modules define none of these names by
    design, so counting their CSS as definitions would make the check vacuous."""
    sources = {f"{COMMON_DIR}{rel}": text for rel, text in STYLED.items()}
    sources[f"{COMMON_DIR}auth/theme.css"] = ":root { --text: #fff; --bg2: #222; }"
    sources["app/src/styles.css"] = ".shell { display: grid }"
    found = check(sources, files=["agent.toml", "app/package.json"], canon=STYLED)
    assert "UI_TOKENS_MISSING" in found


def test_a_token_with_a_fallback_needs_nobody():
    """`var(--x, #fff)` says the module is fine without it. Demanding it anyway is a rule that
    contradicts the code it is checking."""
    canon = {"auth/auth.css": ".card { color: var(--text, #fff); }"}
    assert not styled(".shell { display: grid }", canon=canon)


def test_an_app_with_no_stylesheet_of_its_own_is_not_told_about_tokens():
    """A different problem, and naming twenty missing tokens would bury it. An app with no CSS at
    all did not get its palette wrong."""
    assert not styled(None)


def test_modules_that_read_no_tokens_produce_no_finding():
    canon = {"auth/SignIn.tsx": "export const signIn = 1\n"}
    assert not styled(".shell { display: grid }", canon=canon)

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

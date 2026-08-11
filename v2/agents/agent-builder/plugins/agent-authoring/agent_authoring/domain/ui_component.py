"""UiComponent — a reusable PIECE of an app, addable to an app that already exists.

THE TIER THAT WAS MISSING. This bundle had exactly one kind of reuse: whole-app templates
(``UiTemplate``), copied by ``ScaffoldUiService``, which REFUSES over an existing ``ui/`` — rightly,
because an existing app is somebody's work. So there was no unit smaller than an entire app, and
"add sign-in to an agent that already has a UI" had no route at all: re-scaffold and destroy the
author's work, or hand-edit. Sign-in is not special. It is the first thing that wanted this tier.

A COMPONENT IS A PATCH, NOT A COPY, and that is the whole difference from a template:

    files      its own files, copied from templates/components/<id>/   (may be none)
    borrowed   taken from Agent Builder's LIVE ui/ — the SDK. One copy in the product, so the
               vendored SDK can never disagree with the daemon it talks to.
    scripts    <script src="..."> tags that must exist in index.html, in order
    styles     a token block appended to style.css once, so the component matches the agent's theme
               instead of looking bolted on
    insert     code woven into app.js at a declared anchor
    requires   symbols the vendored SDK must actually export for this component to work

WHY `insert` CARRIES BOTH `anchor` AND `detect`, and why it is a declaration rather than code:

  * ``detect`` makes applying a component IDEMPOTENT. Already there? The step reports
    already-present instead of inserting a second copy. That is what lets the model apply a
    component whenever it is unsure, and what lets the whole catalogue be re-run over every agent.
  * ``anchor`` bounds the guessing. If an app.js has no anchor — hand-written, or older than the
    anchor — the tool does every deterministic step and then STATES the snippet and where it goes.
    It does not regex its way into code it does not recognise. A patch that half-applies is worse
    than an instruction that is followed.

AND THE SNIPPET IS OWNED HERE, once. The sign-in call previously existed in three unshared places:
a literal in the chat-app template, a regex in ``ui_rules``, and (about to be) whatever a component
inserted. Three copies of one fact is precisely the drift the validator exists to catch, so
``UiRules`` is now GIVEN this catalogue — the same argument its own docstring already makes for
event names and gateway methods: "told the real vocabulary rather than keeping their own copy".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# The marker the chat-app template carries. Components insert AFTER it, so the common path is a
# deterministic patch. Its exact text is part of the template contract — a test asserts the
# template contains it, because a typo here silently downgrades every component to instructions.
COMPONENTS_ANCHOR = "AGENTD:COMPONENTS"

# Sign-in needs its OWN anchor, and the reason is a deadlock rather than a preference. The
# components anchor sits inside the "socket is open" branch, which is the right place for anything
# that talks to the daemon — but on a hosted daemon the session token IS the socket credential, so
# a page opened from a marketplace link (`/apps/<id>/`, no token) can never reach that branch: the
# socket is refused until somebody signs in, and the form that signs them in would be waiting for
# the socket. The gate speaks plain HTTP and needs no socket, so it goes BEFORE the connection.
SIGNIN_ANCHOR = "AGENTD:SIGNIN"


@dataclass(frozen=True)
class Insertion:
    """One piece of code a component weaves into a file."""

    file: str  # relative to ui/, e.g. "app.js"
    snippet: str  # the canonical code — the ONE definition of it
    detect: str  # a regex: does this file already have it (in any spelling)?
    anchor: str = COMPONENTS_ANCHOR  # marker comment to insert after
    indent: str = ""  # what to prefix each snippet line with, matched to the anchor's context
    note: str = ""  # shown when the anchor is missing and a human has to place it

    def present_in(self, code: str) -> bool:
        return re.search(self.detect, code) is not None


@dataclass(frozen=True)
class UiComponent:
    id: str
    title: str
    summary: str
    files: tuple[str, ...] = ()  # from templates/components/<id>/
    borrowed: tuple[str, ...] = ()  # from agent-builder's own live ui/
    scripts: tuple[str, ...] = ()  # <script src> paths to ensure in index.html
    styles: str = ""  # CSS appended to style.css (guarded by style_marker)
    style_marker: str = ""  # how to tell the CSS is already there
    insert: tuple[Insertion, ...] = ()
    requires: tuple[str, ...] = ()  # SDK symbols that must exist in the vendored copy
    docs: str = ""  # one paragraph the tool hands back after applying

    @property
    def all_files(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.files) | set(self.borrowed)))


# ── sign-in ─────────────────────────────────────────────────────────────────────────────
#
# No files of its own: the mechanism ships IN the SDK (sdk-js/src/gate.ts). A copy under
# templates/ would put a second version of the gate in one product, and the copy could then
# disagree with the daemon it talks to.
#
# The call is safe unconditionally, which is what makes it a component and not a decision: the gate
# renders NOTHING on a BYOK build, when the device is already connected, or when a stored session
# still works. There is nothing to configure.
SIGN_IN_SNIPPET = """void (async () => {
  try {
    // Sign-in BEFORE the socket. On a hosted daemon the session token is the socket credential,
    // so a page opened from a marketplace link cannot connect until somebody has signed in.
    // Renders NOTHING on a BYOK build, when the page already carries a credential, or when a
    // stored session still works. `client` lets the gate reconnect once a session exists.
    await agentd.mountSignInGate({ client })
  } catch (e) {
    // The daemon itself is unreachable. Not fatal: the status chip reports that too.
    console.warn('[sign-in]', (e && e.message) || e)
  }
})()"""

SIGN_IN = UiComponent(
    id="sign-in",
    title="Hosted sign-in",
    summary=(
        "Lets people use this app on OUR keys instead of pasting their own. Shows a sign-in "
        "panel when the app is running against a hosted platform, and nothing at all otherwise. "
        "Without it, an agent installed from the marketplace fails every model call with a "
        "provider error and nothing on screen explains why."
    ),
    borrowed=("vendor/agentd-client.js",),
    scripts=("vendor/agentd-client.js",),
    style_marker="--gate-bg",
    styles="""
/* sign-in (added by add_ui_component) — the gate reads these, so it matches this app's theme
   instead of looking bolted on. Change the values, keep the names. */
:root {
  --gate-bg: rgba(12, 14, 18, 0.72);
  --gate-card: #14171d;
  --gate-text: #e8eaed;
  --gate-muted: #9aa0a6;
  --gate-accent: #8ab4f8;
  --gate-border: rgba(255, 255, 255, 0.12);
  --gate-radius: 14px;
}
""",
    insert=(
        Insertion(
            file="app.js",
            snippet=SIGN_IN_SNIPPET,
            # Matches the drop-in gate OR direct use of the mechanism, so an app that already
            # signs people in its own way is left alone.
            detect=r"\b(?:mountSignInGate|resolveAuth|platformStatus|signIn)\s*\(",
            anchor=SIGNIN_ANCHOR,
            indent="  ",
            note=(
                "put it BEFORE the connection is wired — right after `agentd.fromPage()` and above "
                "the `client.onStatus(...)` handler, at the top level of the boot function. NOT "
                "inside the `s === 'open'` branch: on a hosted daemon the socket cannot open until "
                "someone has signed in, so a gate that waits for the socket never runs. It is "
                "self-contained (its own async wrapper), so the boot continues without awaiting it."
            ),
        ),
    ),
    requires=("mountSignInGate",),
    docs=(
        "This app can now be used on hosted keys. The panel appears only when the app is pointed "
        "at a platform (accounts_url + model_proxy_url in its distribution.toml) and the user is "
        "not already connected — on a local BYOK build it renders nothing, so there is no branch "
        "to test. Style it with the --gate-* tokens in style.css."
    ),
)


class UiComponents:
    """The catalogue. Same shape as ``UiTemplates`` on purpose.

    The tool's description is generated from ``describe()``, so component #2 is offered to the model
    automatically — adding one is a new entry here and nothing else. That is the difference between
    a reusable tier and a special case: sign-in got a mechanism, not a rollout.
    """

    def __init__(self, components: tuple[UiComponent, ...] = (SIGN_IN,)):
        self._by_id = {c.id: c for c in components}

    def ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    def all(self) -> tuple[UiComponent, ...]:
        return tuple(self._by_id.values())

    def get(self, component_id: str) -> UiComponent | None:
        return self._by_id.get((component_id or "").strip())

    def describe(self) -> str:
        return "\n".join(f"  {c.id} — {c.summary}" for c in self._by_id.values())

    # Used by UiRules so the validator and this catalogue cannot disagree about what "installed"
    # means. Given rather than duplicated — see the module docstring.
    def detectors(self) -> dict[str, tuple[str, ...]]:
        """component id -> the detect patterns that mean it is present."""
        return {c.id: tuple(i.detect for i in c.insert) for c in self._by_id.values()}
